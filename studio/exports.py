"""In-process, single-worker Windows video export with truthful progress.

Status is observable by HTTP polling without blocking the UI request. Only one
export may write videos at once. Percent is weighted by pipeline stages / frames
and should not be mistaken for time remaining.
"""
from __future__ import annotations
from datetime import datetime, timezone
import threading
import uuid

class ExportQueue:
    def __init__(self,service):
        self.service=service
        self._lock=threading.RLock()
        self._jobs={}
        self._running=None

    def start(self,ids,theme="hanji",per_video=6,with_voice=False,voice_engine="auto"):
        if not isinstance(ids,list) or not 1<=len(ids)<=120 or len(set(ids))!=len(ids):
            raise ValueError("Chọn 1–120 câu không trùng.")
        if not isinstance(per_video,int) or not 1<=per_video<=6:
            raise ValueError("Số câu mỗi video phải từ 1 đến 6.")
        if not isinstance(theme,str) or theme not in ("hanji","blue","sejong"):
            raise ValueError("Mẫu video không hợp lệ.")
        if voice_engine not in ("auto","edge","google"):
            raise ValueError("Nguồn giọng đọc không hợp lệ.")
        self.service.store.get_questions(ids,status="approved")
        with self._lock:
            if self._running is not None:
                raise ValueError("Đang xuất video khác. Hãy để lượt hiện tại hoàn thành.")
            jid=uuid.uuid4().hex
            self._jobs[jid]={"job_id":jid,"status":"queued","percent":0,
                  "phase":"Đã vào hàng đợi","videos":[],"error":None,
                  "total_videos":(len(ids)+per_video-1)//per_video,
                  "created_at":datetime.now(timezone.utc).isoformat()}
            self._running=jid
        thread=threading.Thread(target=self._run,
                  args=(jid,ids,theme,per_video,with_voice,voice_engine),
                  daemon=True,name="thse-export-"+jid[:8])
        thread.start()
        return {"job_id":jid,"status":"queued"}

    def status(self,jid):
        with self._lock:
            job=self._jobs.get(jid)
            if job is None:raise ValueError("Không tìm thấy lượt xuất video.")
            return {**job,"videos":list(job["videos"])}

    def _update(self,jid,**changes):
        with self._lock:
            if jid in self._jobs:
                job=self._jobs[jid]
                if "percent" in changes:
                    changes["percent"]=min(100,max(job["percent"],int(changes["percent"])))
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
            self._update(jid,status="completed",percent=100,
                         phase="Tất cả video đã hoàn thành")
        except Exception as exc:
            self._update(jid,status="failed",
                  phase="Đã dừng do lỗi. Video hoàn tất vẫn được giữ.",
                  error=str(exc)[:550])
        finally:
            with self._lock:
                if self._running==jid:self._running=None
                # Keep only the most recent jobs, without removing active job.
                if len(self._jobs)>30:
                    for old in list(self._jobs)[:-30]:
                        if old!=self._running:self._jobs.pop(old,None)
