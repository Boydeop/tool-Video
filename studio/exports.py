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

    def start_repair(self,ids=None):
        if ids is None:ids=[row['id'] for row in self.service.store.pending_verification(6)]
        if (not isinstance(ids,list) or not 1<=len(ids)<=6 or
                any(not isinstance(qid,str) for qid in ids) or len(set(ids))!=len(ids)):
            raise ValueError('Chọn 1–6 câu chưa xuất để tự sửa')
        for qid in ids:self.service.store.repair_source(qid)
        with self._lock:
            if self._running is not None:raise ValueError('Đang có tác vụ sản xuất hoặc sửa câu khác')
            jid=uuid.uuid4().hex
            self.service.store.create_production_run(jid,{'mode':'repair','ids':ids})
            self._started_at[jid]=self._phase_at[jid]=time.monotonic()
            self._jobs[jid]={'job_id':jid,'mode':'repair','status':'queued','percent':0,
                            'phase':'Chờ tự sửa bằng KRDict + AI','videos':[],
                            'results':[],'error':None,'total_videos':0}
            self._running=jid
        threading.Thread(target=self._run_repair,args=(jid,ids),daemon=True,
                         name='thse-repair-'+jid[:8]).start()
        return {'job_id':jid,'mode':'repair','status':'queued'}

    def _run_repair(self,jid,ids):
        from .run_limits import BudgetExpired
        from .krdict import KRDictError
        results=[]
        try:
            for number,qid in enumerate(ids):
                def progress(phase):
                    self._update(jid,status='running',percent=number*100//len(ids),
                                 phase=f'Câu {number+1}/{len(ids)} · '+phase)
                progress('Kiểm tra và tìm từ để sửa')
                try:result=self.service.repair_generated(qid,progress=progress)
                except Exception as exc:
                    result={'id':qid,'status':'needs_review','repaired':False,
                            'repair_issues':[str(exc)[:300] if isinstance(exc,(ValueError,BudgetExpired,KRDictError))
                                             else type(exc).__name__]}
                results.append(result)
                self.service.store.update_production_run(jid,'running',results)
                self._update(jid,results=list(results),percent=(number+1)*100//len(ids))
            repaired=sum(bool(row.get('repaired')) for row in results)
            verified=sum(row['status']=='verified' for row in results)
            self.service.store.update_production_run(jid,'completed',results)
            self._update(jid,status='completed',percent=100,results=results,
                         phase=f'Đã sửa {repaired}/{len(ids)} câu · {verified} câu đạt QA')
        except Exception as exc:
            self.service.store.update_production_run(jid,'failed',results,type(exc).__name__)
            self._update(jid,status='failed',error=type(exc).__name__,results=results)
        finally:
            with self._lock:
                if self._running==jid:self._running=None
                self._finish(jid)

    def start_autopilot(self,config):
        from .autopilot import Plan
        from .events import choose_production_topics
        plan=Plan.parse(config)
        with self._lock:
            if self._running is not None:raise ValueError('Đang có lượt sản xuất khác')
            topics,_=choose_production_topics(self.service.store.topic_counts(),plan.videos,mode=plan.event_mode)
            jid=uuid.uuid4().hex
            self.service.store.create_batch(jid,plan,topics)
            self._launch_batch(jid,plan)
        return {'job_id':jid,'status':'queued','mode':'autopilot'}

    def _launch_batch(self,jid,plan):
        self._cancel_events[jid]=threading.Event()
        self._started_at[jid]=self._phase_at[jid]=time.monotonic()
        self._finished_at.pop(jid,None)
        self._jobs[jid]={'job_id':jid,'mode':'autopilot','status':'queued','percent':0,
            'phase':'Chờ xử lý hàng đợi','videos':[],'error':None,'total_videos':plan.videos,'topics':[]}
        self._running=jid
        threading.Thread(target=self._run_autopilot,args=(jid,plan),daemon=True,
                         name='thse-autopilot-'+jid[:8]).start()

    def resume_autopilot(self,jid,recovery=False):
        from .autopilot import Plan
        with self._lock:
            if self._running is not None:raise ValueError('Đang có tác vụ khác; hãy dừng hoặc chờ hoàn tất')
            run=self.service.store.batch_run(jid)
            if run['plan'].get('mode')!='autopilot':raise ValueError('Không phải lượt sản xuất tự động')
            if run['status']=='completed':raise ValueError('Lượt này đã hoàn tất; không dựng lại video đã có')
            self.service.store.resume_batch(jid,reset_failed=not recovery)
            self._launch_batch(jid,Plan.parse(run['plan']))
        return {'job_id':jid,'mode':'autopilot','status':'queued'}

    def recover_autopilot(self):
        for run in self.service.store.list_production_runs(100):
            if (run['status']=='interrupted' and run['plan'].get('mode')=='autopilot'
                    and self.service.store.batch_tasks(run['id'])):
                return self.resume_autopilot(run['id'],recovery=True)

    def _run_autopilot(self,jid,plan):
        from .batch_runner import BatchRunner
        try:
            BatchRunner(self.service,jid,self._cancel_events[jid],
                        lambda message:self._update(jid,**message)).run()
        except Exception as exc:
            # Preserve the last durable summary if a progress callback fails.
            # Task checkpoints remain authoritative when the run is resumed.
            import json
            run=self.service.store.batch_run(jid)
            self.service.store.update_production_run(jid,'interrupted',json.loads(run['results']),type(exc).__name__)
            self._update(jid,status='needs_retry',error=type(exc).__name__,
                         phase='Đã giữ tiến trình; bấm Tiếp tục để xử lý phần còn lại')
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
                if "percent" in changes and not (job.get('mode')=='autopilot' and
                        changes.get('status') in ('completed','needs_retry','cancelled','blocked')):
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
