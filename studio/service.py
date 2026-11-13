"""Business workflow: daily curriculum, unique AI drafts and video publishing."""
from datetime import date
from pathlib import Path
from .ai import fallback
from .database import DuplicateQuestion,Store
from .quiz import TOPICS, daily_plan, choose_random_topic
from .renderer import render

class Service:
    def __init__(self,root="data"):
        self.root=Path(root).resolve()
        import os
        os.environ["THSE_KEYS_FILE"]=str(self.root/"keys.local.json")
        os.environ["THSE_MODELS_FILE"]=str(self.root/"models.local.json")
        self.root.mkdir(parents=True,exist_ok=True)
        self.store=Store(self.root/"studio.sqlite3")

    def suggest_topic(self, exclude=None, level="TOPIK 1"):
        # The preview and generated topic remain consistent when the UI
        # submits the selected topic. No free-form typing is necessary.
        return {"topic": choose_random_topic(self.store.topic_counts(), exclude),
                "level": level, "topics_available": len(TOPICS)}

    def generate_random(self, level="TOPIK 1", count=6, exclude=None):
        selected = self.suggest_topic(exclude=exclude, level=level)
        result = self.generate(selected["topic"], level, count)
        return {"plan": selected, **result}

    def list_videos(self, limit=50):
        folder = self.root / "videos"
        if not folder.is_dir():
            return []
        files = sorted(folder.glob("*.mp4"), key=lambda p: p.stat().st_mtime,
                       reverse=True)
        return [{"name": path.name, "path": str(path.resolve()),
                 "size_mb": round(path.stat().st_size / 1048576, 1)}
                for path in files[:limit] if path.is_file()]

    def generate(self,topic,level,count=6,fmt="từ vựng",providers=None):
        if not 1<=count<=30: raise ValueError("Request 1-30 questions")
        accepted=[]
        errors=[]
        for attempt in range(3):
            remaining=count-len(accepted)
            if remaining<=0: break
            recent=self.store.recent_prompts(50)
            try:
                provider,rows=fallback(topic,level,min(30,remaining+min(remaining,4)),
                                       recent,fmt,providers)
            except Exception as exc:
                errors.append(str(exc))
                break
            for quiz in rows:
                try:
                    qid=self.store.insert_question(quiz,provider)
                    accepted.append(qid)
                except DuplicateQuestion:
                    pass
                if len(accepted)>=count: break
        self.store.log("generation",f"{topic}: accepted={len(accepted)}/{count}")
        return {"accepted":accepted,"requested":count,"errors":errors,
                "message":"Drafts need manual accuracy review before rendering."}

    def daily(self,day=None,count=6,providers=None):
        today=day or date.today()
        plan=daily_plan(today,self.store.topic_counts())
        if self.store.daily_exists(today.isoformat()):
            return {"skipped":True,"reason":"Day already generated","plan":plan}
        outcome=self.generate(plan["topic"],plan["level"],count,plan["format"],providers)
        # Only mark complete after at least one draft is created.
        if outcome["accepted"]:
            self.store.daily_record(today.isoformat(),plan["topic"],outcome)
        return {"plan":plan,**outcome}

    def render_approved(self,ids,theme="hanji",filename=None,width=540,with_voice=False):
        questions=self.store.get_questions(ids,status="approved")
        if not questions: raise ValueError("No approved questions")
        name=filename or "video_"+__import__("uuid").uuid4().hex[:12]+".mp4"
        if Path(name).name != name or not name.endswith(".mp4"):
            raise ValueError("Invalid filename")
        target=self.root/"videos"/name
        return render(questions,target,theme,width,with_voice=with_voice)

    def schedule(self,video_path,caption,at):
        # Do not schedule video outside this application's output folder.
        root=(self.root/"videos").resolve()
        p=Path(video_path).resolve()
        if root not in p.parents:
            raise ValueError("Only generated videos in data/videos may be scheduled")
        return self.store.add_job(p,caption,at)
