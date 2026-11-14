"""Bounded, no-per-question-checkbox production pipeline for Korean Shorts.

AI text is machine-checked, not pedagogically verified. Run is user-triggered,
records all rejected/missing items, and never posts without a separate opt-in.
"""
from __future__ import annotations
import random
from dataclasses import dataclass
from .quiz import TOPICS,LEVELS,FORMATS
from .quality import inspect_quiz

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
    event_mode:str="random"
    qa_mode:str="strict"

    @classmethod
    def parse(cls,raw:dict):
        if not isinstance(raw,dict):raise ValueError("Invalid plan")
        levels=raw.get("levels",["TOPIK 1"])
        formats=raw.get("formats",["từ vựng"])
        plan=cls(videos=int(raw.get("videos",3)),per_video=int(raw.get("per_video",4)),
                 levels=tuple(levels),formats=tuple(formats),
                 theme=str(raw.get("theme","hanji")),
                 voice=raw.get("with_voice") is True,
                 engine=str(raw.get("voice_engine","auto")),
                 width=int(raw.get("width",1080)),seo=raw.get("auto_seo") is True,
                 event_mode=str(raw.get("event_mode","random")),
                 qa_mode=str(raw.get("qa_mode","strict")))
        if not 1<=plan.videos<=12 or not 1<=plan.per_video<=6:
            raise ValueError("Maximum 12 videos per run, 1-6 questions/video")
        if not plan.levels or not all(x in LEVELS for x in plan.levels):
            raise ValueError("Invalid TOPIK levels")
        if not plan.formats or not all(x in FORMATS for x in plan.formats):
            raise ValueError("Invalid quiz formats")
        if plan.theme not in ("hanji","blue","sejong") or plan.width not in (540,1080):
            raise ValueError("Invalid video preset")
        if plan.event_mode not in ("random","mix","events"):
            raise ValueError("Chế độ chủ đề sự kiện không hợp lệ")
        if plan.qa_mode not in ("strict","assisted"):
            raise ValueError("Chế độ kiểm chứng không hợp lệ")
        if plan.engine not in ("auto","edge","google"):
            raise ValueError("Invalid voice engine")
        return plan

    def public_dict(self):
        return {"videos":self.videos,"per_video":self.per_video,
                "levels":list(self.levels),"formats":list(self.formats),
                "theme":self.theme,"with_voice":self.voice,
                "voice_engine":self.engine,"width":self.width,
                "review_policy":"strict machine check, NOT human verified",
                "auto_seo":self.seo,"event_mode":self.event_mode,
                "qa_mode":self.qa_mode}

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

def run_pipeline(service,plan:Plan,update,on_result=None):
    """One independent topic per video; keep failed/draft attempts visible."""
    from .events import choose_production_topics
    chosen,events=choose_production_topics(service.store.topic_counts(),plan.videos,
                                           mode=plan.event_mode)
    results=[]
    rejected=[]
    for index,topic in enumerate(chosen):
        level=plan.levels[index%len(plan.levels)]
        fmt=plan.formats[index%len(plan.formats)]
        update(100*index/plan.videos,"AI tạo chủ đề "+str(index+1)+"/"+str(plan.videos))
        item={"topic":topic,"level":level,"format":fmt,"status":"draft",
              "video":"","accepted":0,"reason":""}
        results.append(item)
        try:
            good=[]
            problems=[]
            generation_errors=[]
            inspected=set()
            repairs=0
            # First request includes spare candidates to survive duplicate or
            # incomplete AI answers. A second bounded attempt is optional.
            for attempt in range(2):
                if len(good)>=plan.per_video:break
                remaining=plan.per_video-len(good)
                count=min(12,max(remaining+2,remaining))
                try:
                    generated=service.generate(topic,level,count,fmt=fmt)
                    generation_errors.extend(generated.get("errors",[]))
                except Exception as exc:
                    generation_errors.append(type(exc).__name__+": "+str(exc)[:200])
                    break
                incoming=[qid for qid in generated.get("accepted",[]) if qid not in inspected]
                if not incoming:
                    break
                for qid in incoming:
                    inspected.add(qid)
                    quiz=service.store.get_questions([qid])[0]
                    issues=inspect_quiz(quiz)
                    if (quiz.missing_answer_details and
                            repairs<plan.per_video+2):
                        repairs+=1
                        try:
                            service.enrich_answer_details([qid])
                            quiz=service.store.get_questions([qid])[0]
                            issues=inspect_quiz(quiz)
                        except Exception as exc:
                            generation_errors.append("Bổ sung "+qid[:6]+": "+
                                type(exc).__name__)
                    if issues:
                        service.store.set_question_qa(qid,"needs_review",
                            {"issues":issues,"stage":"structure","version":1})
                        problems.append({"id":qid,"issues":issues})
                        continue
                    try:
                        verdict=service.verify_generated(qid,mode=plan.qa_mode)
                        if verdict["status"]=="verified":
                            # Unrendered verified spare candidates remain ready.
                            if len(good)<plan.per_video:good.append(qid)
                        else:
                            problems.append({"id":qid,"issues":
                                  verdict.get("issues") or ["Chưa đủ bằng chứng từ KRDict/AI"]})
                    except Exception as exc:
                        service.store.set_question_qa(qid,"unavailable",
                          {"issues":[type(exc).__name__],"stage":"verification"})
                        problems.append({"id":qid,"issues":["Lỗi kiểm chứng: "+type(exc).__name__]})
            item["accepted"]=len(good)
            item["requires_review"]=problems
            item["generation_errors"]=generation_errors[-4:]
            if len(good)<plan.per_video:
                item["status"]="needs_review"
                kinds={}
                for candidate in problems:
                    for issue in candidate["issues"]:
                        kinds[issue]=kinds.get(issue,0)+1
                examples="; ".join(k+": "+str(v) for k,v in
                                  sorted(kinds.items(),key=lambda x:-x[1])[:3])
                if not examples and generation_errors:
                    examples=generation_errors[-1]
                item["reason"]=("Chỉ có "+str(len(good))+"/"+str(plan.per_video)+
                    " câu đạt kiểm tra. "+(examples or
                    "API chưa trả đủ câu mới hoặc nội dung trùng.")+
                    " Các câu chưa đạt vẫn lưu nháp.")
                rejected.extend(problems)
                update(100*(index+1)/plan.videos,item["reason"])
                continue
            good=good[:plan.per_video]
            def frame_progress(percent,phase):
                update((index+max(0,min(1,percent/100)))*100/plan.videos,phase)
            output=service.render_approved(good,theme=plan.theme,width=plan.width,
                    with_voice=plan.voice,voice_engine=plan.engine,
                    progress=frame_progress)
            item["status"]="rendered";item["video"]=output
            if plan.seo:
                try:
                    metadata=service.auto_seo_for_video(output)
                    item["seo_source"]=metadata["source"]
                except Exception as seo_exc:
                    # A metadata draft failure must never erase an exported MP4.
                    item["seo_source"]="unavailable"
                    item["seo_error"]=type(seo_exc).__name__
            update(100*(index+1)/plan.videos,"Đã xuất "+str(index+1)+"/"+str(plan.videos))
        except Exception as exc:
            item["status"]="failed"
            item["reason"]=type(exc).__name__+": "+str(exc)[:280]
            update(100*(index+1)/plan.videos,"Lỗi chủ đề "+str(index+1)+": "+str(exc)[:100])
        finally:
            if on_result:on_result([dict(row) for row in results])
    return {"videos":[r["video"] for r in results if r["video"]],
            "topics":results,"needs_review":rejected,
            "rendered":sum(bool(r["video"]) for r in results)}
