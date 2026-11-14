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
    max_attempts:int=3

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
                 qa_mode=str(raw.get("qa_mode","strict")),
                 max_attempts=int(raw.get("max_attempts",3)))
        if not 1<=plan.videos<=100 or not 1<=plan.per_video<=6:
            raise ValueError("Maximum 100 videos per run, 1-6 questions/video")
        if not 1<=plan.max_attempts<=5:raise ValueError("Chỉ thử lại tối đa 5 lần/video")
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
                "qa_mode":self.qa_mode,"max_attempts":self.max_attempts}

def choose_distinct_topics(counts,number,rng=None):
    if not 1<=number<=100:raise ValueError("Choose 1–100 video topics")
    source=rng or random.SystemRandom()
    pool=list(TOPICS)
    selected=[]
    for _ in range(number):
        if not pool:pool=list(TOPICS)
        weights=[1/(1+max(0,counts.get(topic,0)))**1.5 for topic in pool]
        topic=source.choices(pool,weights=weights,k=1)[0]
        selected.append(topic);pool.remove(topic)
    return selected

def run_pipeline(service,plan:Plan,update,on_result=None,cancel=None,topics=None):
    """Bounded QA per topic, visible substeps, preserving partial MP4 outputs.

    Cancellation is cooperative; an FFmpeg render already underway is allowed
    to finish its current MP4 before the worker stops.
    """
    from .events import choose_production_topics
    from .run_limits import (topic_budget,check_budget,check_cancel,
                             BudgetExpired,RunCancelled)
    if topics is not None:
        if len(topics)!=plan.videos:raise ValueError('Topic count mismatch')
        chosen=topics
    else:
        chosen,_events=choose_production_topics(service.store.topic_counts(),plan.videos,mode=plan.event_mode)
    results=[]
    rejected=[]
    for index,topic in enumerate(chosen):
        if cancel is not None and cancel.is_set():break
        level=plan.levels[index%len(plan.levels)]
        fmt=plan.formats[index%len(plan.formats)]
        item={"topic":topic,"level":level,"format":fmt,"status":"draft",
              "video":"","accepted":0,"reason":""}
        results.append(item)
        def stage(message,fraction=0):
            value=(index+min(.97,max(0.0,fraction)))*100/plan.videos
            update(value,"Chủ đề "+str(index+1)+"/"+str(plan.videos)+
                   " · "+message)
        stage("Bắt đầu tạo câu hỏi",0)
        try:
            good=[]
            problems=[]
            generation_errors=[]
            inspected=set()
            repairs=0
            # The budget bounds network generation + review, not frame render.
            # Previously 100-second sequential AI calls could accumulate to
            # more than 30 minutes without a stage update.
            with topic_budget(210,cancel):
                try:
                    for attempt in range(3):
                        check_budget()
                        if len(good)>=plan.per_video:
                            break
                        remaining=plan.per_video-len(good)
                        count=min(10,remaining+2)
                        stage("AI tạo câu (lượt "+str(attempt+1)+"/3)",.03+.10*attempt)
                        try:
                            generated=service.generate(topic,level,count,fmt=fmt)
                            generation_errors.extend(generated.get("errors",[]))
                            if generated.get('retry_after'):item['retry_after']=generated['retry_after']
                            if generated.get('blocked'):item['blocked']=True
                        except (BudgetExpired,RunCancelled):
                            raise
                        except Exception as exc:
                            generation_errors.append(type(exc).__name__+
                                                     ": "+str(exc)[:200])
                            break
                        incoming=[qid for qid in generated.get("accepted",[])
                                  if qid not in inspected]
                        if not incoming:
                            if generated.get("errors"):break
                            continue
                        for number,qid in enumerate(incoming,1):
                            check_budget()
                            if len(good)>=plan.per_video:
                                break  # don't spend minutes checking surplus
                            inspected.add(qid)
                            stage("Kiểm chứng KRDict + AI, câu "+
                                  str(number)+"/"+str(len(incoming)),
                                  .20+.55*(number/max(1,len(incoming))))
                            quiz=service.store.get_questions([qid])[0]
                            issues=inspect_quiz(quiz)
                            if (quiz.missing_answer_details and
                                    repairs<plan.per_video+1):
                                repairs+=1
                                stage("Bổ sung phiên âm/nghĩa câu "+
                                      str(number)+"/"+str(len(incoming)),.25)
                                try:
                                    service.enrich_answer_details([qid])
                                    quiz=service.store.get_questions([qid])[0]
                                    issues=inspect_quiz(quiz)
                                except (BudgetExpired,RunCancelled):
                                    raise
                                except Exception as exc:
                                    generation_errors.append("Bổ sung "+qid[:6]+
                                        ": "+type(exc).__name__)
                            try:
                                verdict=service.verify_generated(qid,mode=plan.qa_mode)
                                if verdict.get('blocked') or verdict.get('retry_after'):
                                    item['blocked']=verdict.get('blocked',False)
                                    item['retry_after']=verdict.get('retry_after',0)
                                    problems.append({'id':qid,'issues':verdict.get('issues',[]) or ['Dịch vụ kiểm chứng chưa khả dụng']})
                                    break  # Outage is not incorrect vocabulary to repair.
                                if verdict["status"]!="verified":
                                    stage("Tự sửa bằng KRDict + AI, câu "+str(number),.55)
                                    verdict=service.repair_generated(qid,initial=verdict,
                                        progress=lambda message:stage(message,.60))
                                    if verdict.get("repaired"):
                                        item.setdefault("repaired",[]).append({"id":qid,"changes":verdict["changes"]})
                                if verdict["status"]=="verified":
                                    good.append(qid)
                                else:
                                    problems.append({"id":qid,"issues":
                                        (verdict.get("repair_issues",[])+verdict.get("issues",[])) or
                                        ["Chưa đủ bằng chứng từ KRDict/AI"]})
                            except (BudgetExpired,RunCancelled):
                                raise
                            except Exception as exc:
                                service.store.set_question_qa(qid,"unavailable",
                                    {"issues":[type(exc).__name__],
                                     "stage":"verification"})
                                problems.append({"id":qid,"issues":
                                      ["Lỗi kiểm chứng: "+type(exc).__name__]})
                        if item.get('blocked') or item.get('retry_after'):break
                except BudgetExpired as exc:
                    generation_errors.append(str(exc))
                    stage("Dừng kiểm chứng vì quá thời gian cho chủ đề",.84)
                check_budget() if len(good)>=plan.per_video else None
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
                item["reason"]=("Chỉ có "+str(len(good))+"/"+
                    str(plan.per_video)+" câu đủ điều kiện. "+
                    (examples or "API chưa trả đủ câu hoặc câu trùng.")+
                    " Tác vụ sẽ tạo lại nội dung nếu còn lượt thử.")
                rejected.extend(problems)
                stage(item["reason"],.98)
                continue
            check_cancel(cancel)
            stage("Đang dựng MP4 từ "+str(len(good))+" câu đã kiểm chứng",.78)
            def frame_progress(percent,phase):
                stage(phase,.78+.19*max(0,min(1,percent/100)))
            output=service.render_approved(good[:plan.per_video],
                  theme=plan.theme,width=plan.width,
                  with_voice=plan.voice,voice_engine=plan.engine,
                  progress=frame_progress)
            item["status"]="rendered";item["video"]=output
            check_cancel(cancel)
            if plan.seo:
                stage("Tạo bản nháp tiêu đề SEO",.98)
                try:
                    with topic_budget(35,cancel):
                        metadata=service.auto_seo_for_video(output)
                    item["seo_source"]=metadata["source"]
                except RunCancelled:
                    raise
                except Exception as exc:
                    item["seo_source"]="unavailable"
                    item["seo_error"]=type(exc).__name__
            stage("Đã xuất MP4 "+str(index+1)+"/"+str(plan.videos),.99)
        except RunCancelled as exc:
            if not item["video"]:
                item["status"]="cancelled"
            item["reason"]=str(exc)
            stage("Đã dừng theo yêu cầu",.99)
            break
        except Exception as exc:
            if not item["video"]:
                item["status"]="failed"
            item["reason"]=type(exc).__name__+": "+str(exc)[:280]
            stage("Lỗi: "+str(exc)[:120],.99)
        finally:
            if on_result:on_result([dict(row) for row in results])
        if cancel is not None and cancel.is_set():
            break
    return {"videos":[r["video"] for r in results if r["video"]],
            "topics":results,"needs_review":rejected,
            "rendered":sum(bool(r["video"]) for r in results)}
