"""Deterministic, sortable, Windows-safe output filenames and Shorts profiles."""
from __future__ import annotations
from datetime import datetime,timezone,timedelta
import re
import unicodedata

SEO_PREFIX = "THSE"
PROFILES={
    "shorts_1080":{"width":1080,"height":1920,"fps":30,"safe":True},
    "shorts_540":{"width":540,"height":960,"fps":24,"safe":True},
}
LOCAL_TIMEZONE=timezone(timedelta(hours=7))  # Vietnam has no DST

def slug(value:str,max_length=36)->str:
    if not isinstance(value,str):raise ValueError("Invalid topic")
    # 'đ' needs explicit mapping before NFKD.
    ascii_value=unicodedata.normalize("NFKD",value.replace("Đ","D").replace("đ","d"))
    ascii_value="".join(c for c in ascii_value if not unicodedata.combining(c))
    ascii_value=re.sub(r"[^A-Za-z0-9]+","-",ascii_value).strip("-").lower()
    return ascii_value[:max_length].strip("-") or "tong-hop"

def video_name(sequence:int,topic:str,level:str="TOPIK 1",width:int=1080,day=None)->str:
    if not isinstance(sequence,int) or not 1<=sequence<=999999:
        raise ValueError("Invalid production sequence")
    if width not in (540,1080):raise ValueError("Unsupported resolution")
    local=day or datetime.now(LOCAL_TIMEZONE)
    label=re.sub(r"[^A-Z0-9]","",str(level).upper())[:8] or "TOPIK1"
    return f"{SEO_PREFIX}_{local:%Y%m%d}_{sequence:06d}_{slug(topic)}_{label}_{width}x{width*16//9}.mp4"
