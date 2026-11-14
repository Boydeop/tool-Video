"""Local scheduler with at-most-once dispatch and manual reconciliation."""
import threading
from .publisher import publish_reel
from .youtube import YouTubeAccount
from .instagram import InstagramAccount

class Scheduler:
    def __init__(self,store,interval=30):
        self.store,self.interval=store,interval
        self.youtube=YouTubeAccount(store.path.parent)
        self.instagram=InstagramAccount(store.path.parent)
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
        for job in self.store.claim_outbound():
            try:
                if job["platform"]=="youtube":
                    remote=self.youtube.upload(job["video_path"],job["title"],
                                               job["caption"],job["privacy"])
                elif job["platform"]=="instagram":
                    remote=self.instagram.publish(job["public_url"],job["caption"])
                else:
                    raise RuntimeError("Unknown outbound platform")
                self.store.finish_outbound(job["id"],"submitted",remote_id=remote)
                if job["platform"]=="youtube":
                    self.store.enqueue_youtube_thumbnail(job["video_path"],remote)
                self.store.log("platform_submitted",job["platform"]+" "+job["id"])
            except Exception as exc:
                # It is unsafe to repeat an uncertain remote upload automatically.
                self.store.finish_outbound(job["id"],"needs_review",
                                           error=type(exc).__name__+": "+str(exc)[:500])
                self.store.log("platform_needs_review",job["platform"]+" "+job["id"])
        # Separate from MP4 upload. A failed cover image never uploads the
        # same video twice. YouTube Shorts allow thumbnails after processing.
        for thumbnail in self.store.claim_youtube_thumbnails(limit=1):
            try:
                stage=self.youtube.processing_status(thumbnail["video_id"])
                if stage=="succeeded":
                    self.youtube.set_thumbnail(thumbnail["video_id"],thumbnail["image_path"])
                    self.store.update_youtube_thumbnail(thumbnail["video_id"],success=True)
                elif stage=="failed":
                    self.store.update_youtube_thumbnail(thumbnail["video_id"],
                        error="YouTube processing failed",retry=False)
                else:
                    self.store.update_youtube_thumbnail(thumbnail["video_id"],
                        error="YouTube video processing: "+stage,retry=True)
            except Exception as exc:
                # Only image is retried; MP4 is not touched.
                self.store.update_youtube_thumbnail(thumbnail["video_id"],
                    error=type(exc).__name__+": "+str(exc)[:130],retry=True)
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
