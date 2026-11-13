"""Local scheduler with at-most-once dispatch and manual reconciliation."""
import threading
from .publisher import publish_reel

class Scheduler:
    def __init__(self,store,interval=30):
        self.store,self.interval=store,interval
        self.stopped=threading.Event()
        self.thread=None
    def tick(self):
        for job in self.store.claim_due():
            try:
                remote=publish_reel(job["video_path"],job["caption"],title=job.get("title",""))
                self.store.finish_job(job["id"],"submitted",remote_id=remote)
                self.store.log("submitted_to_meta",job["id"])
            except Exception as exc:
                self.store.finish_job(job["id"],"needs_review",error=str(exc))
                self.store.log("needs_review",job["id"])
    def run(self):
        while not self.stopped.is_set():
            try: self.tick()
            except Exception as exc: self.store.log("scheduler_error",type(exc).__name__)
            self.stopped.wait(self.interval)
    def start(self):
        if self.thread and self.thread.is_alive(): return
        self.thread=threading.Thread(target=self.run,daemon=True)
        self.thread.start()
    def stop(self):
        self.stopped.set()
        if self.thread: self.thread.join(timeout=5)
