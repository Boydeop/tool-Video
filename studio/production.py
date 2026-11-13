"""Local production settings, file selection and uploaded logo storage."""
from __future__ import annotations
import base64
import io
import json
import os
from pathlib import Path
import threading
from PIL import Image

_LOCK=threading.RLock()
DEFAULTS={"think":5.5,"reveal":2.8,"transition":0.8,"intro":1.5,
          "fps":30,"width":1080,"sfx":True,"sfx_volume":0.34,
          "logo_position":"top-right","logo_scale":0.15,
          "logo_x":0.82,"logo_y":0.10,"shorts_safe":True,
          "metadata_title":"Tiếng Hàn So Easy",
          "metadata_artist":"Tiếng Hàn So Easy",
          "metadata_copyright":"© Tiếng Hàn So Easy. All rights reserved.",
          "metadata_comment":"tienghansoeasy.io.vn"}
POSITIONS=("top-right","top-left","bottom-right","bottom-left","custom")

class Production:
    def __init__(self,root):
        self.root=Path(root).resolve()
        self.root.mkdir(parents=True,exist_ok=True)
        self.file=self.root/"production.local.json"
    def settings(self):
        data=DEFAULTS.copy()
        if self.file.is_file():
            try:
                contents=json.loads(self.file.read_text(encoding="utf-8"))
                if isinstance(contents,dict):data.update(contents)
            except (OSError,ValueError):pass
        data["export_dir"]=str(Path(data.get("export_dir") or self.root/"videos").resolve())
        data["cache_dir"]=str(Path(data.get("cache_dir") or self.root/"cache").resolve())
        history=data.get("export_history",[])
        data["export_history"]=[str(Path(p).expanduser().resolve()) for p in history[:20]
                                if isinstance(p,str)] if isinstance(history,list) else []
        data["has_logo"]=(self.root/"brand_logo.png").is_file()
        return data
    def update(self,values):
        current=self.settings()
        allowed=set(DEFAULTS)|{"export_dir","cache_dir"}
        if not isinstance(values,dict) or any(k not in allowed for k in values):
            raise ValueError("Thiết lập không hợp lệ.")
        new={**current,**values}
        if "export_dir" in values and str(Path(values["export_dir"]).resolve())!=current["export_dir"]:
            history=[current["export_dir"],*current.get("export_history",[])]
            new["export_history"]=list(dict.fromkeys(history))[:20]
        for key in ("think","reveal","transition","intro","logo_scale","sfx_volume","logo_x","logo_y"):
            new[key]=float(new[key])
        if not (4<=new["think"]<=12 and 1.5<=new["reveal"]<=7
                and .3<=new["transition"]<=2 and .5<=new["intro"]<=4
                and .06<=new["logo_scale"]<=.28 and 0<=new["sfx_volume"]<=1
                and 0<=new["logo_x"]<=1 and 0<=new["logo_y"]<=1):
            raise ValueError("Thời gian, kích thước logo hoặc âm lượng SFX ngoài giới hạn.")
        new["fps"]=int(new["fps"])
        new["width"]=int(new["width"])
        if new["fps"] not in (24,30) or new["width"] not in (540,1080):
            raise ValueError("Chỉ hỗ trợ 540/1080p và 24/30 FPS.")
        if (new["logo_position"] not in POSITIONS or not isinstance(new["sfx"],bool)
            or not isinstance(new["shorts_safe"],bool)):
            raise ValueError("Vị trí logo hoặc SFX không hợp lệ.")
        for field in ("metadata_title","metadata_artist","metadata_copyright","metadata_comment"):
            if not isinstance(new[field],str) or len(new[field])>220 or any(ord(ch)<32 for ch in new[field]):
                raise ValueError("Metadata video không hợp lệ hoặc quá dài.")
        for fld in ("export_dir","cache_dir"):
            val=Path(new[fld]).expanduser().resolve()
            if not val.is_absolute():raise ValueError("Cần đường dẫn tuyệt đối.")
            val.mkdir(parents=True,exist_ok=True)
            if not val.is_dir():raise ValueError("Đường dẫn không phải thư mục.")
            new[fld]=str(val)
        keep={key:new[key] for key in allowed}
        keep["export_history"]=new.get("export_history",[])
        with _LOCK:
            tmp=self.file.with_suffix(".tmp")
            tmp.write_text(json.dumps(keep,ensure_ascii=False,indent=2),encoding="utf-8")
            tmp.replace(self.file)
        return self.settings()
    def logo(self):
        p=self.root/"brand_logo.png"
        return p if p.is_file() else None
    def set_logo(self,b64):
        if not isinstance(b64,str) or len(b64)>4_000_000:
            raise ValueError("Logo quá lớn.")
        try:
            data=base64.b64decode(b64,validate=True)
            if len(data)>2_000_000:raise ValueError("Logo tối đa 2 MB.")
            img=Image.open(io.BytesIO(data))
            img.verify()
            img=Image.open(io.BytesIO(data)).convert("RGBA")
            if img.width>3000 or img.height>3000 or img.width<4 or img.height<4:
                raise ValueError("Kích thước logo không hợp lệ.")
            img.thumbnail((900,900),Image.Resampling.LANCZOS)
            target=self.root/"brand_logo.png"
            img.save(target,format="PNG")
            return {"logo":True,"width":img.width,"height":img.height}
        except (OSError,ValueError) as exc:
            raise ValueError("File logo phải là PNG/JPEG/WebP hợp lệ dưới 2 MB.") from exc
    def choose_directory(self,kind):
        if kind not in ("export_dir","cache_dir"):
            raise ValueError("Thư mục không hợp lệ.")
        # Windows common dialog runs locally; browser file input can't reveal a
        # real absolute path for security reasons.
        import tkinter as tk
        from tkinter import filedialog
        root=tk.Tk();root.withdraw();root.attributes("-topmost",True)
        try:
            selected=filedialog.askdirectory(parent=root,initialdir=self.settings()[kind],
                                             title="Chọn thư mục "+("xuất video" if kind=="export_dir" else "bộ nhớ đệm"))
        finally:root.destroy()
        if not selected:return {"cancelled":True}
        return self.update({kind:selected})
