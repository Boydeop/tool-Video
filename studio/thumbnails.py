"""Deterministic portrait thumbnail from the video's genuine opening design."""
from __future__ import annotations
from pathlib import Path
import os
from .renderer import draw_intro

def thumbnail_path(video_path):
    video=Path(video_path).resolve()
    if video.suffix.lower()!=".mp4":
        raise ValueError("Expected MP4 path")
    return video.with_name(video.stem+"_thumbnail.jpg")

def ensure_thumbnail(video_path,topic,level,theme,settings,logo=None,force=False):
    video=Path(video_path).resolve()
    if not video.is_file():
        raise ValueError("Cannot create a thumbnail before MP4 exists")
    target=thumbnail_path(video)
    if target.is_file() and not force:return target
    width=int(settings.get("width",1080))
    width=width if width in (540,1080) else 1080
    im=draw_intro(theme,width,logo_path=logo,
        logo_position=settings.get("logo_position","top-right"),
        logo_scale=float(settings.get("logo_scale",.15)),
        logo_x=float(settings.get("logo_x",.82)),
        logo_y=float(settings.get("logo_y",.10)),
        shorts_safe=bool(settings.get("shorts_safe",True)),
        topic=str(topic)[:70],level=str(level)[:25])
    if im.width<1080:
        im=im.resize((1080,1920),__import__("PIL.Image",fromlist=["Image"]).Resampling.LANCZOS)
    # The thumbnail contains the real intro scene, no AI hallucinated objects.
    staged=target.with_suffix(".pending.jpg")
    try:
        staged.parent.mkdir(parents=True,exist_ok=True)
        im.convert("RGB").save(staged,"JPEG",quality=92,optimize=True,subsampling=0)
        if staged.stat().st_size>3_000_000:
            raise ValueError("Thumbnail unexpectedly large")
        os.replace(staged,target)
    finally:
        staged.unlink(missing_ok=True)
    return target
