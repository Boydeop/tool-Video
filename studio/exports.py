"""In-process, single-worker Windows video export with truthful progress.

Status is observable by HTTP polling without blocking the UI request. Only one
export may write videos at once. Percent is weighted by pipeline stages / frames
and should not be mistaken for time remaining.
"""
from __future__ import annotations
from datetime import datetime, timezone
import threading
import time
import uuid

class ExportQueue:
    def __init__(self,service):
        self.service=service
        self._lock=threading.RLock()
        self._jobs={}
        self._running=None
        self._cancel_events={}
        self._started_at={}
        self._phase_at={}
        self._finished_at={}

    def start(self,ids,theme="hanji",per_video=6,with_voice=False,voice_engine="auto"):
        if not isinstance(ids,list) or not 1<=len(ids)<=120 or len(set(ids))!=len(ids):
            raise ValueError("Chọn 1–120 câu không trùng.")
        if not isinstance(per_video,int) or not 1<=per_video<=6:
            raise ValueError("Số câu mỗi video phải từ 1 đến 6.")
        if not isinstance(theme,str) or theme not in ("hanji","blue","sejong"):
            raise ValueError("Mẫu video không hợp lệ.")
        if voice_engine not in ("auto","edge","google"):
            raise ValueError("Nguồn giọng đọc không hợp lệ.")
        self.service.store.get_questions(ids,status=("approved","auto_approved"))
        with self._lock:
            if self._running is not None:
                raise ValueError("Đang xuất video khác. Hãy để lượt hiện tại hoàn thành.")
            jid=uuid.uuid4().hex
            self._started_at[jid]=time.monotonic()
            self._phase_at[jid]=self._started_at[jid]
            self._jobs[jid]={"job_id":jid,"status":"queued","percent":0,
                  "phase":"Đã vào hàng đợi","videos":[],"error":None,
                  "total_videos":(len(ids)+per_video-1)//per_video,
                  "created_at":datetime.now(timezone.utc).isoformat()}
            self._running=jid
            try:
                self.service.store.create_production_run(
                    jid,{"mode":"manual","per_video":per_video,
                         "question_count":len(ids),"theme":theme})
            except Exception:
                self._running=None
                self._jobs.pop(jid,None)
                raise
        thread=threading.Thread(target=self._run,
                  args=(jid,ids,theme,per_video,with_voice,voice_engine),
                  daemon=True,name="thse-export-"+jid[:8])
        thread.start()
        return {"job_id":jid,"status":"queued"}

    def is_running(self):
        with self._lock:
            return self._running is not None

    def start_autopilot(self,config):
        from .autopilot import Plan
        plan=Plan.parse(config)
        with self._lock:
            if self._running is not None:
                raise ValueError("Đang có lượt xuất video khác.")
            jid=uuid.uuid4().hex
            self.service.store.create_production_run(jid,{"mode":"autopilot",**plan.public_dict()})
            self._cancel_events[jid]=threading.Event()
            self._started_at[jid]=time.monotonic()
            self._phase_at[jid]=self._started_at[jid]
            self._jobs[jid]={"job_id":jid,"status":"queued","percent":0,
                "phase":"Chờ tạo nhiều chủ đề","videos":[],"error":None,
                "total_videos":plan.videos,"topics":[],
                "created_at":datetime.now(timezone.utc).isoformat()}
            self._running=jid
        threading.Thread(target=self._run_autopilot,args=(jid,plan),
                 daemon=True,name="thse-autopilot-"+jid[:8]).start()
        return {"job_id":jid,"status":"queued","mode":"autopilot"}

    def _run_autopilot(self,jid,plan):
        from .autopilot import run_pipeline
        results=[]
        cancel_event=self._cancel_events[jid]
        try:
            self._update(jid,status="running",phase="Đang sinh câu hỏi nhiều chủ đề")
            def progress(percent,phase):
                self._update(jid,percent=min(98,int(percent)),phase=phase)
            def store_results(items):
                nonlocal results
                results=items
                outputs=[item.get("video") for item in items if item.get("video")]
                self._update(jid,topics=items,videos=outputs)
                self.service.store.update_production_run(jid,"running",items)
            completed=run_pipeline(self.service,plan,progress,store_results,
                                   cancel=cancel_event)
            outputs=completed["videos"]
            # No video with saved drafts is a review-needed run, not a broken
            # renderer. Surface actual file count instead of treating 100%
            # stages processed as 100% successfully exported.
            terminal=("completed" if len(outputs)==plan.videos else
                      "cancelled" if cancel_event.is_set() else
                      "partial" if outputs else "needs_review")
            self.service.store.update_production_run(jid,
                   "partial" if terminal in ("needs_review","cancelled") else terminal,
                   results)
            self._update(jid,status=terminal,percent=100,
                         phase=("Đã dừng an toàn: " if terminal=="cancelled" else
                                "Đã kiểm tra chủ đề, còn câu cần bổ sung: "
                                if terminal=="needs_review" else "Đã kết thúc Autopilot: ")+
                               str(len(outputs))+"/"+str(plan.videos)+" MP4",
                         videos=outputs,topics=results)
        except Exception as exc:
            self.service.store.update_production_run(jid,"failed",results,str(exc))
            self._update(jid,status="failed",error=str(exc)[:500],
                         phase="Autopilot gặp lỗi, xem câu đã tạo và video đã xuất")
        finally:
            with self._lock:
                if self._running==jid:self._running=None
                self._cancel_events.pop(jid,None)
                self._finish(jid)

    def cancel_autopilot(self,jid):
        with self._lock:
            if jid!=self._running or jid not in self._cancel_events:
                raise ValueError("Không có Autopilot đang chạy để dừng.")
            self._cancel_events[jid].set()
            job=self._jobs[jid]
            if job["status"] in ("queued","running","stopping"):
                self._update(jid,status="stopping",
                    phase="Đã yêu cầu dừng · chờ hoàn tất tác vụ hiện tại")
            return {"job_id":jid,"status":"stopping",
                    "message":"Đang dừng an toàn. MP4 đã hoàn thành không bị xóa."}

    def active(self):
        with self._lock:
            return self.status(self._running) if self._running else {"active":False}

    def status(self,jid):
        with self._lock:
            job=self._jobs.get(jid)
            if job is None:raise ValueError("Không tìm thấy lượt xuất video.")
            now=self._finished_at.get(jid,time.monotonic())
            return {**job,"videos":list(job["videos"]),
                    "elapsed_seconds":int(now-self._started_at.get(jid,now)),
                    "phase_elapsed_seconds":int(now-self._phase_at.get(jid,now))}

    def _finish(self,jid):
        """Bound both job history and its timing/cancellation metadata."""
        self._finished_at[jid]=time.monotonic()
        for old in list(self._jobs)[:-30]:
            if old!=self._running:
                for mapping in (self._jobs,self._started_at,self._phase_at,
                                self._finished_at,self._cancel_events):
                    mapping.pop(old,None)

    def _update(self,jid,**changes):
        with self._lock:
            if jid in self._jobs:
                job=self._jobs[jid]
                if "percent" in changes:
                    changes["percent"]=min(100,max(job["percent"],int(changes["percent"])))
                if ("phase" in changes and changes["phase"]!=job.get("phase")):
                    self._phase_at[jid]=time.monotonic()
                job.update(changes)

    def _run(self,jid,ids,theme,per_video,with_voice,voice_engine):
        done=0
        try:
            self._update(jid,status="running",phase="Đang khởi tạo quá trình xuất")
            for start in range(0,len(ids),per_video):
                subset=ids[start:start+per_video]
                step_count=len(subset)
                def notify(percentage,phase,start=start,step_count=step_count):
                    overall=(start+step_count*min(1,max(0,percentage/100)))*100/len(ids)
                    self._update(jid,percent=round(overall),phase=phase)
                path=self.service.render_approved(subset,theme=theme,
                     with_voice=with_voice,voice_engine=voice_engine,
                     progress=notify)
                done+=step_count
                with self._lock:
                    self._jobs[jid]["videos"].append(path)
                self._update(jid,percent=round(done*100/len(ids)),
                       phase="Đã hoàn thành video "+str(len(self._jobs[jid]["videos"])))
                self.service.store.update_production_run(jid,"running",
                     [{"video":v,"status":"rendered"} for v in self._jobs[jid]["videos"]])
            self.service.store.update_production_run(jid,"completed",
                 [{"video":v,"status":"rendered"} for v in self._jobs[jid]["videos"]])
            self._update(jid,status="completed",percent=100,
                         phase="Tất cả video đã hoàn thành")
        except Exception as exc:
            try:
                videos=self.status(jid)["videos"]
                self.service.store.update_production_run(jid,
                     "partial" if videos else "failed",
                     [{"video":v,"status":"rendered"} for v in videos],str(exc))
            except Exception:
                pass
            self._update(jid,status="failed",
                  phase="Đã dừng do lỗi. Video hoàn tất vẫn được giữ.",
                  error=str(exc)[:550])
        finally:
            with self._lock:
                if self._running==jid:self._running=None
                self._finish(jid)
