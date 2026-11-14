"""MP4 metadata with broad player compatibility and fail-safe verification.

Use the standard iTunes/ilst MP4 atoms, not arbitrary mdta keys. Windows
Explorer only displays some MP4 tags depending on shell handler and codec.
Copyright metadata is attribution, NOT DRM or legal proof of ownership.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os
import subprocess
import uuid

from .fftools import binary, hidden_subprocess

TAG_MAP={
    "metadata_title":"title",
    "metadata_artist":"artist",
    "metadata_copyright":"copyright",
    "metadata_comment":"comment",
}
REQUIRED=("title","artist","copyright","comment")
TZ_VIETNAM=timezone(timedelta(hours=7))

def _probe(path):
    task=subprocess.run([binary("ffprobe"),"-hide_banner","-v","error",
                "-show_entries","format=duration:format_tags",
                "-of","json",str(path)],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        timeout=45,check=False,**hidden_subprocess())
    if task.returncode:
        raise RuntimeError("FFprobe không đọc được MP4: "+
                           task.stderr.decode("utf-8","replace")[-350:])
    try:
        data=json.loads(task.stdout)
        info=data["format"]
        if float(info["duration"])<=0:raise ValueError("invalid duration")
        return info.get("tags",{})
    except (ValueError,KeyError,TypeError) as exc:
        raise RuntimeError("MP4 hoặc metadata không hợp lệ.") from exc

def _mp4_tag_layout(path):
    # Fast-start MP4 stores its metadata box near the start of the file.
    # This is a compatibility hint, not a formal parser or proof of ownership.
    with open(path,"rb") as stream:
        beginning=stream.read(8*1024*1024)
    # FFmpeg writes an ilst wrapper for both conventions. The handler
    # 'mdta' distinguishes legacy key/value atoms from standard 'mdir' ilst.
    if b"mdta" in beginning:return "mdta"
    if b"ilst" in beginning:return "ilst"
    return "unknown"

def inspect_metadata(filename):
    source=Path(filename).resolve()
    if source.suffix.lower()!=".mp4" or not source.is_file():
        raise ValueError("Không tìm thấy MP4.")
    tags=_probe(source)
    # Some MP4 tag names may have case differences depending on FFprobe.
    normalized={str(k).casefold():str(v) for k,v in tags.items()}
    return {
        "filename":source.name,
        "tags":{key:normalized.get(key,"") for key in (*REQUIRED,"date","genre","description")},
        "verified":all(normalized.get(key,"") for key in REQUIRED),
        "layout":_mp4_tag_layout(source),
        "note":"Windows Explorer chỉ hiển thị một số trường MP4; dùng FFprobe để kiểm tra chính xác.",
    }

def overwrite_metadata(filename,fields):
    source=Path(filename).resolve()
    if source.suffix.lower()!=".mp4" or not source.is_file():
        raise ValueError("Chỉ hỗ trợ file MP4 có sẵn.")
    tags={}
    for setting,tag in TAG_MAP.items():
        value=fields.get(setting,"")
        if not isinstance(value,str) or len(value)>220 or any(ord(c)<32 for c in value):
            raise ValueError("Metadata không hợp lệ.")
        tags[tag]=value.strip()
    if not all(tags.values()):
        raise ValueError("Thiếu title, artist, copyright hoặc comment trong metadata.")
    year=str(datetime.now(TZ_VIETNAM).year)
    extra={"date":year,"genre":"Education",
           "description":tags["title"]+" | "+tags["copyright"]+" | "+tags["comment"]}
    target=source.with_name(source.stem+".tags-"+uuid.uuid4().hex[:8]+".mp4")
    cmd=[binary("ffmpeg"),"-hide_banner","-loglevel","error","-y",
         "-i",str(source),"-map","0","-c","copy","-map_metadata","-1",
         # Do NOT use +use_metadata_tags: the resulting mdta payload is
         # readable by FFprobe but often invisible in Windows Explorer.
         "-movflags","+faststart"]
    for key,value in {**tags,**extra}.items():
        cmd.extend(["-metadata",key+"="+value])
    cmd.append(str(target))
    try:
        result=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                              timeout=240,check=False,**hidden_subprocess())
        if result.returncode or not target.is_file() or target.stat().st_size<100:
            raise RuntimeError("Ghi metadata FFmpeg thất bại: "+
                               result.stderr.decode("utf-8","replace")[-700:])
        readback={str(key).casefold():str(value) for key,value in _probe(target).items()}
        for key,value in tags.items():
            if readback.get(key)!=value:
                raise RuntimeError("MP4 không lưu được trường "+key+
                                   ". Giữ nguyên video cũ, không ghi đè.")
        if readback.get("date")!=year:
            raise RuntimeError("MP4 không lưu được metadata năm.")
        try:os.replace(target,source)
        except PermissionError as exc:
            raise RuntimeError("Hãy đóng video trong Media Player/Explorer rồi thử lại.") from exc
        return {"video":str(source),"tags":{**tags,**extra},
                "verified":True,"format":"MP4 ilst / standard atoms",
                "note":"Windows Explorer chỉ hiển thị một phần thẻ MP4; kiểm tra lại bằng FFprobe."}
    finally:
        target.unlink(missing_ok=True)
