"""Sequential, durable video queue with bounded retry and restart recovery."""
from dataclasses import replace
import time
from .autopilot import Plan,run_pipeline
from .run_limits import RunCancelled

class BatchRunner:
    def __init__(self,service,run_id,cancel,on_update,*,clock=time.time,wait=None,processor=None,backoff=None):
        self.service,self.store=service,service.store
        self.run_id,self.cancel,self.on_update=run_id,cancel,on_update
        self.clock=clock;self.wait=wait or cancel.wait
        self.processor=processor or self._produce
        self.backoff=backoff or (lambda attempt:min(60,5*2**(attempt-1)))
        self.plan=Plan.parse(self.store.batch_run(run_id)['plan'])
        self.tasks=self.store.batch_tasks(run_id)
        self._last_phase=None;self._last_publish=0

    def _produce(self,task,progress):
        with self.service.production_scope(task):
            return run_pipeline(self.service,replace(self.plan,videos=1,
                levels=(task['level'],),formats=(task['format'],)),progress,
                cancel=self.cancel,topics=[task['topic']])

    def snapshot(self,status,phase,*,persist=True,percent=None):
        if persist:self.tasks=self.store.batch_tasks(self.run_id)
        results=[{'task_id':t['id'],'slot':t['slot']+1,'topic':t['topic'],
                  'status':t['status'],'attempts':t['attempts'],'max_attempts':t['max_attempts'],
                  'video':t['video_path'],'reason':t['error'],'next_attempt':t['next_attempt']}
                 for t in self.tasks]
        videos=[t['video_path'] for t in self.tasks if t['status']=='completed']
        if persist:
            state={'needs_retry':'partial','cancelled':'paused'}.get(status,status)
            self.store.update_production_run(self.run_id,state,results)
        message={'mode':'autopilot','status':status,'phase':phase,'topics':results,
                 'videos':videos,'total_videos':self.plan.videos,
                 'percent':int(len(videos)*100/self.plan.videos) if percent is None else percent,
                 'failed_count':sum(t['status'] in ('failed','blocked') for t in self.tasks),
                 'pending_count':sum(t['status'] in ('queued','retry_wait','running') for t in self.tasks)}
        self.on_update(message)
        return message

    def run(self):
        self.snapshot('running','Bắt đầu hàng đợi · video đã hoàn tất sẽ không dựng lại')
        while not self.cancel.is_set():
            task=self.store.claim_batch_task(self.run_id,self.clock())
            if task is None:
                self.tasks=self.store.batch_tasks(self.run_id)
                pending=[t for t in self.tasks if t['status'] in ('queued','retry_wait') and t['attempts']<t['max_attempts']]
                if not pending:break
                remaining=max(0,min(t['next_attempt'] for t in pending)-self.clock())
                phase=f'Đang chờ dịch vụ phục hồi · thử lại sau {int(remaining)+1}s'
                if phase!=self._last_phase:
                    self.snapshot('running',phase,persist=False);self._last_phase=phase
                self.wait(min(1,max(.05,remaining)))
                continue
            self.snapshot('running',f"Video {task['slot']+1}/{self.plan.videos} · lần {task['attempts']}/{task['max_attempts']}")
            def progress(percent,phase):
                now=self.clock()
                if phase!=self._last_phase or now-self._last_publish>=.5:
                    done=sum(t['status']=='completed' for t in self.tasks)
                    self.snapshot('running',f"Video {task['slot']+1}/{self.plan.videos} · lần {task['attempts']} · {phase}",
                                  persist=False,percent=min(99,int((done+max(0,min(98,percent))/100)*100/self.plan.videos)))
                    self._last_publish=now;self._last_phase=phase
            blocked=False;delay=0;reason='Chưa tạo đủ câu đạt QA';result=None
            try:
                self.service.prepare_production_task(task)
                result=self.processor(task,progress)
            except RunCancelled:
                self.cancel.set()
            except Exception as exc:
                reason=str(exc)[:700] if isinstance(exc,(ValueError,RuntimeError)) else type(exc).__name__
            # The atomic registry checkpoint wins even if thumbnail, SEO,
            # cancellation or a late callback failed after the MP4 was committed.
            latest=next(t for t in self.store.batch_tasks(self.run_id) if t['id']==task['id'])
            if latest['status']=='completed':
                self.store.discard_task_questions(task['id'])
                self.snapshot('running',f"Đã xong {sum(t['status']=='completed' for t in self.store.batch_tasks(self.run_id))}/{self.plan.videos} MP4")
                continue
            self.store.discard_task_questions(task['id'])
            try:self.service.discard_production_output(task)
            except OSError:
                # A locked orphan stays hidden from the library; the next
                # attempt retries removal before any new generation starts.
                reason='Không dọn được MP4 tạm; kiểm tra ứng dụng đang giữ file'
            if self.cancel.is_set():
                self.store.pause_batch_task(task['id']);break
            if result:
                item=(result.get('topics') or [{}])[0]
                reason=item.get('reason') or reason
                blocked=bool(item.get('blocked'))
                delay=float(item.get('retry_after') or 0)
            provider_wait=delay>0
            delay=max(delay,self.backoff(task['attempts']))
            self.store.fail_batch_task(task['id'],reason,delay,blocked,self.clock(),shared=provider_wait)
            if blocked:
                return self.snapshot('blocked','Cần kiểm tra key/model trong Kết nối · sau đó bấm Tiếp tục')
            self.snapshot('running','Tác vụ chưa đạt · đã bỏ nội dung tạm và lên lịch tạo lại từ đầu')
        tasks=self.store.batch_tasks(self.run_id)
        done=sum(t['status']=='completed' for t in tasks)
        if done==self.plan.videos:return self.snapshot('completed',f'Hoàn tất {done}/{self.plan.videos} MP4')
        if self.cancel.is_set():return self.snapshot('cancelled',f'Đã tạm dừng · giữ {done}/{self.plan.videos} MP4 · có thể tiếp tục')
        return self.snapshot('needs_retry',f'Đã xong {done}/{self.plan.videos} MP4 · các mục chưa xong có thể thử lại')
