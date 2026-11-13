"""Production workflow: batches, media render, preview, cache and SEO drafts."""
from datetime import date
import io
import json
import os
from pathlib import Path
import uuid

from .ai import fallback
from .database import DuplicateQuestion,Store
from .facebook import credentials
from .production import Production
from .quiz import TOPICS, daily_plan, choose_random_topic
from .renderer import render,draw_frame

class Service:
    def __init__(self,root="data"):
        self.root=Path(root).resolve()
        self.root.mkdir(parents=True,exist_ok=True)
        os.environ["THSE_KEYS_FILE"]=str(self.root/"keys.local.json")
        os.environ["THSE_MODELS_FILE"]=str(self.root/"models.local.json")
        os.environ["THSE_FACEBOOK_PATH"]=str(self.root/"facebook.local.json")
        self.production=Production(self.root)
        self.store=Store(self.root/"studio.sqlite3")

    def suggest_topic(self,exclude=None,level="TOPIK 1"):
        return {"topic":choose_random_topic(self.store.topic_counts(),exclude),
                "level":level,"topics_available":len(TOPICS)}

    def generate_random(self,level="TOPIK 1",count=6,exclude=None):
        plan=self.suggest_topic(exclude,level)
        return {"plan":plan,**self.generate(plan["topic"],level,count)}

    def generate(self,topic,level,count=6,fmt="từ vựng",providers=None):
        if not 1<=count<=30:raise ValueError("Số câu mỗi lần phải từ 1 tới 30.")
        accepted=[];errors=[];batch_id=uuid.uuid4().hex[:12]
        for attempt in range(3):
            remaining=count-len(accepted)
            if remaining<=0:break
            try:
                provider,rows=fallback(topic,level,min(30,remaining+min(remaining,4)),
                                       self.store.recent_prompts(50),fmt,providers)
            except Exception as exc:
                errors.append(str(exc))
                break
            for quiz in rows:
                try:accepted.append(self.store.insert_question(quiz,provider,batch_id))
                except DuplicateQuestion:pass
                if len(accepted)>=count:break
        self.store.log("generation",f"{topic}: accepted={len(accepted)}/{count}, batch={batch_id}")
        return {"accepted":accepted,"requested":count,"batch_id":batch_id,
                "errors":errors,"message":"Review each Korean answer before rendering."}

    def daily(self,day=None,count=6,providers=None):
        current=day or date.today();plan=daily_plan(current,self.store.topic_counts())
        if self.store.daily_exists(current.isoformat()):
            return {"skipped":True,"reason":"Day already generated","plan":plan}
        result=self.generate(plan["topic"],plan["level"],count,plan["format"],providers)
        if result["accepted"]:
            self.store.daily_record(current.isoformat(),plan["topic"],result)
        return {"plan":plan,**result}

    def video_path(self,name):
        if not isinstance(name,str) or Path(name).name!=name or not name.endswith(".mp4"):
            raise ValueError("Tên video không hợp lệ.")
        directory=Path(self.production.settings()["export_dir"]).resolve()
        path=(directory/name).resolve()
        if directory not in path.parents or not path.is_file():
            raise ValueError("Không tìm thấy MP4.")
        return path

    def list_videos(self,limit=80):
        settings=self.production.settings()
        folder=Path(settings["export_dir"])
        existing=sorted(folder.glob("*.mp4"),key=lambda p:p.stat().st_mtime,reverse=True)
        registry={str(Path(r["path"]).resolve()):r for r in self.store.list_videos(limit=500)}
        result=[]
        for file in existing[:limit]:
            info=registry.get(str(file.resolve())) or {}
            result.append({"name":file.name,"path":str(file.resolve()),
                "size_mb":round(file.stat().st_size/1048576,1),
                "status":info.get("status","legacy"),
                "seo_title":info.get("seo_title",""),
                "seo_caption":info.get("seo_caption",""),
                "topic":info.get("topic","")})
        # Imported legacy files under the original output folder remain discoverable.
        old=self.root/"videos"
        if old.resolve()!=folder.resolve() and old.is_dir():
            for f in sorted(old.glob("*.mp4"),reverse=True)[:25]:
                result.append({"name":f.name,"path":str(f.resolve()),"size_mb":round(f.stat().st_size/1048576,1),
                               "status":"legacy","seo_title":"","seo_caption":"","topic":""})
        return result

    def render_approved(self,ids,theme="hanji",filename=None,width=None,
                        with_voice=False,voice_engine="auto"):
        questions=self.store.get_questions(ids,status="approved")
        if not questions:raise ValueError("Chọn ít nhất một câu đã duyệt.")
        config=self.production.settings()
        name=filename or "video_"+uuid.uuid4().hex[:12]+".mp4"
        if Path(name).name!=name or not name.endswith(".mp4"):
            raise ValueError("Tên file xuất không hợp lệ.")
        path=Path(config["export_dir"])/name
        if path.exists():raise ValueError("Tên file đã tồn tại.")
        completed=render(
            questions,path,theme=theme,width=width or config["width"],
            fps=config["fps"],think=config["think"],reveal=config["reveal"],
            with_voice=with_voice,voice_cache=config["cache_dir"],
            voice_engine=voice_engine,transition=config["transition"],
            intro=config["intro"],sfx=config["sfx"],sfx_volume=config["sfx_volume"],
            logo_path=self.production.logo(),logo_position=config["logo_position"],
            logo_scale=config["logo_scale"])
        self.store.mark_rendered(ids,completed)
        return completed

    def preview(self,question_id,theme="hanji",reveal_answer=True):
        from PIL import Image
        if theme not in ("hanji","blue","sejong"):raise ValueError("Mẫu giao diện không hợp lệ.")
        q=self.store.get_questions([question_id])[0]
        config=self.production.settings()
        elapsed=config["think"]+.6 if reveal_answer else config["think"]-.5
        im=draw_frame(q,1,1,theme,elapsed,config["think"],540,
            config["reveal"],config["transition"],self.production.logo(),
            config["logo_position"],config["logo_scale"])
        buf=io.BytesIO()
        im.save(buf,format="PNG")
        return buf.getvalue()

    def preview_motion(self,question_id,theme="hanji"):
        questions=self.store.get_questions([question_id])
        cfg=self.production.settings()
        folder=Path(cfg["cache_dir"])/"video_previews"
        folder.mkdir(parents=True,exist_ok=True)
        path=folder/("preview_"+uuid.uuid4().hex[:12]+".mp4")
        render(questions,path,theme=theme,width=540,fps=cfg["fps"],
               think=cfg["think"],reveal=cfg["reveal"],
               intro=cfg["intro"],transition=cfg["transition"],sfx=cfg["sfx"],
               sfx_volume=cfg["sfx_volume"],with_voice=False,
               logo_path=self.production.logo(),logo_position=cfg["logo_position"],
               logo_scale=cfg["logo_scale"])
        for old in sorted(folder.glob("preview_*.mp4"),
                          key=lambda x:x.stat().st_mtime,reverse=True)[20:]:
            old.unlink(missing_ok=True)
        return path

    def preview_clip_path(self,name):
        if not name.startswith("preview_") or not name.endswith(".mp4") or Path(name).name!=name:
            raise ValueError("Preview file không hợp lệ.")
        folder=Path(self.production.settings()["cache_dir"])/"video_previews"
        path=(folder/name).resolve()
        if path.parent!=folder.resolve() or not path.is_file():
            raise ValueError("Preview đã hết hạn.")
        return path

    def render_many(self,ids,theme="hanji",per_video=6,with_voice=False,
                    voice_engine="auto"):
        if not isinstance(ids,list) or not 1<=len(ids)<=120 or len(set(ids))!=len(ids):
            raise ValueError("Chọn từ 1 đến 120 câu không trùng.")
        if not 1<=per_video<=6:
            raise ValueError("Chia từ 1 đến 6 câu cho mỗi video Reels.")
        self.store.get_questions(ids,status="approved")
        completed=[]
        for i in range(0,len(ids),per_video):
            try:
                output=self.render_approved(ids[i:i+per_video],theme=theme,
                                            with_voice=with_voice,voice_engine=voice_engine)
                completed.append(output)
            except Exception as exc:
                return {"videos":completed,"failed_at":i,
                        "error":str(exc)[:500],"partial":True}
        return {"videos":completed,"partial":False}

    def seo_video(self,name):
        from .seo import draft
        path=self.video_path(name)
        item=self.store.get_video(path)
        ids=json.loads(item["question_ids"])
        result=draft(self.store.get_questions(ids))
        self.store.set_video_seo(path,result["title"],result["caption"])
        return result

    def schedule(self,video_path,caption,at,title=""):
        target=Path(video_path).resolve()
        folders=[Path(self.production.settings()["export_dir"]).resolve(),
                 (self.root/"videos").resolve()]
        if target.parent not in folders or not target.is_file() or target.suffix.lower()!=".mp4":
            raise ValueError("Video không thuộc thư mục xuất của Studio.")
        page,token=credentials()
        if not page or not token:
            raise ValueError("Chưa kết nối Facebook Page. Vào tab Lịch đăng Page.")
        if len(title)>160 or len(caption)>2000:
            raise ValueError("Tiêu đề hoặc mô tả quá dài.")
        job_id=self.store.add_job(target,caption,at,title)
        try:self.store.mark_scheduled(target)
        except ValueError:pass  # Legacy video, already tracked in jobs.
        return job_id
