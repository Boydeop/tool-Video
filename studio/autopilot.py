"""Resilient, bounded Autopilot with optional calendar events.

Strategy:
- Distinct normal topics OR distinct subtopics of an upcoming dated event.
- Generate AI drafts, enrich missing option details if keys are available, retry.
- Fill gaps from a small explicitly-labelled curated vocabulary seed, without
  falsely treating generated translations as teacher-verified.
- Render 2+ fully structured questions rather than reject a whole batch
  because the AI returned 4/5 usable answers.
- Preserve drafts and per-question reasons; no automatic publishing.
"""
from __future__ import annotations
import random
import uuid
from dataclasses import dataclass
from .quiz import TOPICS,LEVELS,FORMATS
from .quality import inspect_quiz
from .events import resolve_event,event_topics
from .curated_content import fill as curated_fill, available_bank_topics, BANKS

@dataclass(frozen=True)
class Plan:
    videos:int=3
    per_video:int=4
    levels:tuple[str,...]=("TOPIK 1",)
    formats:tuple[str,...]=("từ vựng",)
    theme:str="hanji"
    voice:bool=False
    engine:str="auto"
    width:int=1080
    seo:bool=False
    topic_mode:str="random"
    event_id:str=""
    auto_enrich:bool=True
    curated_fallback:bool=True
    min_questions:int=2

    @classmethod
    def parse(cls,raw:dict):
        if not isinstance(raw,dict):raise ValueError("Invalid plan")
        levels=raw.get("levels",["TOPIK 1"])
        formats=raw.get("formats",["từ vựng"])
        if not isinstance(levels,(list,tuple)) or not isinstance(formats,(list,tuple)):
            raise ValueError("Levels/formats must be lists")
        plan=cls(videos=int(raw.get("videos",3)),per_video=int(raw.get("per_video",4)),
                 levels=tuple(levels),formats=tuple(formats),
                 theme=str(raw.get("theme","hanji")),voice=raw.get("with_voice") is True,
                 engine=str(raw.get("voice_engine","auto")),
                 width=int(raw.get("width",1080)),seo=raw.get("auto_seo") is True,
                 topic_mode=str(raw.get("topic_mode","random")),
                 event_id=str(raw.get("event_id","")),
                 auto_enrich=raw.get("auto_enrich") is not False,
                 curated_fallback=raw.get("curated_fallback") is not False,
                 min_questions=int(raw.get("min_questions",min(2,int(raw.get("per_video",4))))))
        if not 1<=plan.videos<=12 or not 1<=plan.per_video<=6:
            raise ValueError("Maximum 12 videos per run, 1-6 questions/video")
        if not 1<=plan.min_questions<=plan.per_video:
            raise ValueError("Invalid minimum question count")
        if not plan.levels or not all(x in LEVELS for x in plan.levels):
            raise ValueError("Invalid TOPIK levels")
        if not plan.formats or not all(x in FORMATS for x in plan.formats):
            raise ValueError("Invalid quiz formats")
        if plan.theme not in ("hanji","blue","sejong") or plan.width not in (540,1080):
            raise ValueError("Invalid video preset")
        if plan.engine not in ("auto","edge","google"):
            raise ValueError("Invalid voice engine")
        if plan.topic_mode not in ("random","seasonal"):
            raise ValueError("Chọn chế độ chủ đề ngẫu nhiên hoặc sự kiện.")
        if plan.topic_mode=="seasonal":
            resolve_event(plan.event_id)  # Disallow stale or invented lunar events.
        return plan

    def public_dict(self):
        return {"videos":self.videos,"per_video":self.per_video,
                "levels":list(self.levels),"formats":list(self.formats),
                "theme":self.theme,"with_voice":self.voice,
                "voice_engine":self.engine,"width":self.width,"auto_seo":self.seo,
                "topic_mode":self.topic_mode,"event_id":self.event_id,
                "auto_enrich":self.auto_enrich,"curated_fallback":self.curated_fallback,
                "min_questions":self.min_questions,
                "review_policy":"AI/seed machine checked, NOT human-verified"}

def choose_distinct_topics(counts,number,rng=None):
    if number>len(TOPICS):raise ValueError("Too many distinct topics")
    source=rng or random.SystemRandom()
    pool=list(TOPICS)
    selected=[]
    for _ in range(number):
        weights=[1/(1+max(0,counts.get(topic,0)))**1.5 for topic in pool]
        topic=source.choices(pool,weights=weights,k=1)[0]
        selected.append(topic);pool.remove(topic)
    return selected

def _available_ai_keys():
    try:
        from .keys import key_counts
        return any(key_counts().values())
    except (OSError,ValueError):
        return False

def run_pipeline(service,plan:Plan,update,on_result=None):
    counts=service.store.topic_counts()
    event=resolve_event(plan.event_id) if plan.topic_mode=="seasonal" else None
    topics=(event_topics(event["id"],plan.videos) if event
            else choose_distinct_topics(counts,plan.videos))
    results=[]
    rejected=[]
    used_topics=set()
    enrich_budget=min(8,plan.per_video*2) if plan.auto_enrich and _available_ai_keys() else 0
    for index,topic in enumerate(topics):
        level=plan.levels[index%len(plan.levels)]
        fmt=plan.formats[index%len(plan.formats)]
        item={"topic":topic,"level":level,"format":fmt,"status":"draft",
              "video":"","accepted":0,"requested":plan.per_video,
              "reason":"","source":"AI","issues":[],"event":event}
        results.append(item)
        batch_id="auto_"+uuid.uuid4().hex[:9]
        ids=[];good=[];seen=set()
        errors=[]
        try:
            # Extra calls request only missing questions. Never run unbounded AI loops.
            for attempt in range(3):
                missing=plan.per_video-len(good)
                if missing<=0:break
                update((index+.04+.14*attempt)*100/plan.videos,
                       "AI: "+topic+" · lượt "+str(attempt+1)+"/3")
                try:
                    generated=service.generate(topic,level,missing,fmt=fmt)
                except Exception as exc:
                    errors.append("AI "+type(exc).__name__+": "+str(exc)[:140])
                    break
                errors.extend(str(x)[:170] for x in generated.get("errors",[])[:2])
                created=generated.get("accepted",[])
                if not created:
                    break
                new_count=0
                for qid in created:
                    if qid in seen:continue
                    seen.add(qid);ids.append(qid);new_count+=1
                    q=service.store.get_questions([qid])[0]
                    issues=inspect_quiz(q)
                    if issues and plan.auto_enrich and enrich_budget>0:
                        # Only enrich missing semantic fields, not fundamentally
                        # ambiguous or malformed Korean answers.
                        if any("Thiếu nghĩa" in issue for issue in issues):
                            enrich_budget-=1
                            try:
                                service.enrich_answer_details([qid])
                                q=service.store.get_questions([qid])[0]
                                issues=inspect_quiz(q)
                            except Exception as exc:
                                errors.append("Enrich "+type(exc).__name__)
                    if not issues and len(good)<plan.per_video:
                        good.append(qid)
                    else:
                        item["issues"].append({"id":qid,"issues":issues or ["Câu dự phòng chưa dùng"]})
                if not new_count:break
            # Use explicit, per-term known data only when model is incomplete.
            missing=plan.per_video-len(good)
            if missing>0 and plan.curated_fallback:
                fallback_topic=topic
                if not event and topic not in BANKS:
                    # When an unsupported AI topic fails completely, switch
                    # transparently to a different available curated topic.
                    if len(good)<plan.min_questions:
                        choices=available_bank_topics(counts,set(topics)|used_topics)
                        if choices:fallback_topic=choices[0]
                if fallback_topic!=topic:
                    # Do not combine questions of unrelated topics in one MP4.
                    good=[]
                    item["topic_replaced_from"]=topic
                    topic=fallback_topic
                    item["topic"]=topic
                curated=curated_fill(service.store,topic,level,plan.per_video-len(good),
                                     event_id=event["id"] if event else None,
                                     batch_id=batch_id)
                for qid in curated:
                    q=service.store.get_questions([qid])[0]
                    issues=inspect_quiz(q)
                    if not issues:
                        good.append(qid)
                    else:
                        item["issues"].append({"id":qid,"issues":issues})
                if curated:
                    item["source"]="AI + curated_fallback" if ids and "topic_replaced_from" not in item else "curated_fallback"
            item["accepted"]=len(good)
            if len(good)<plan.min_questions:
                item["status"]="needs_review"
                item["reason"]=(str(len(good))+"/"+str(plan.per_video)+
                    " câu đạt kiểm tra (cần ít nhất "+str(plan.min_questions)+"). "+
                    "Xem nháp để sửa; lỗi API: "+"; ".join(errors[-2:]))
                rejected.extend(item["issues"])
                update(100*(index+1)/plan.videos,item["reason"])
                continue
            good=good[:plan.per_video]
            service.store.auto_approve(good)
            def progress(percentage,phase):
                update((index+max(0,min(1,percentage/100)))*100/plan.videos,phase)
            output=service.render_approved(good,theme=plan.theme,width=plan.width,
                   with_voice=plan.voice,voice_engine=plan.engine,progress=progress)
            item["video"]=output
            item["status"]="rendered" if len(good)==plan.per_video else "rendered_short"
            if item["status"]=="rendered_short":
                item["reason"]=("Đã tạo "+str(len(good))+"/"+str(plan.per_video)+
                                " câu đạt kiểm tra; video ngắn hơn yêu cầu.")
            if plan.seo:
                try:
                    meta=service.auto_seo_for_video(output)
                    item["seo_source"]=meta["source"]
                except Exception as exc:
                    item["seo_source"]="unavailable"
                    item["seo_error"]=type(exc).__name__
            used_topics.add(topic)
            update(100*(index+1)/plan.videos,
                   "Đã xuất "+str(index+1)+"/"+str(plan.videos)+": "+topic)
        except Exception as exc:
            item["status"]="failed"
            item["reason"]=type(exc).__name__+": "+str(exc)[:250]
            update(100*(index+1)/plan.videos,"Lỗi chủ đề "+topic+": "+str(exc)[:90])
        finally:
            item["errors"]=errors[-3:]
            if on_result:on_result([dict(row) for row in results])
    return {"videos":[r["video"] for r in results if r["video"]],
            "topics":results,"needs_review":rejected,
            "rendered":sum(bool(r["video"]) for r in results)}
