"""Write and replace MP4 descriptive metadata without touching encoded media.

Metadata is editable attribution, NOT DRM, watermarking, Content ID or
legal proof of ownership. Never overwrite the input until remux succeeds.
"""
from pathlib import Path
import os
import subprocess
import uuid
from .fftools import binary,hidden_subprocess

TAG_MAP={
    "metadata_title":"title",
    "metadata_artist":"artist",
    "metadata_copyright":"copyright",
    "metadata_comment":"comment"
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
        tags[tag]=value
    target=source.with_name(source.stem+".tags-"+uuid.uuid4().hex[:8]+".mp4")
    cmd=[binary("ffmpeg"),"-hide_banner","-loglevel","error","-y",
         "-i",str(source),"-map","0","-c","copy","-map_metadata","-1",
         "-movflags","+faststart+use_metadata_tags"]
    for key,value in tags.items():
        cmd.extend(["-metadata",key+"="+value])
    # 'author' is an alias not recognized equally by all MP4 readers.
    cmd+=["-metadata","author="+tags["artist"],str(target)]
    try:
        result=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                              timeout=240,check=False,**hidden_subprocess())
        if result.returncode or not target.is_file() or target.stat().st_size<100:
            raise RuntimeError("Ghi metadata FFmpeg thất bại: "+result.stderr.decode("utf-8","replace")[-700:])
        # Do not corrupt the source if the tool produced an invalid media container.
        verify=subprocess.run([binary("ffprobe"),"-v","error","-show_entries",
            "format=duration","-of","default=nk=1:nw=1",str(target)],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,
            check=False,**hidden_subprocess())
        if verify.returncode or not verify.stdout.strip():
            raise RuntimeError("Video sau khi ghi metadata không đọc được.")
        os.replace(target,source)
        return {"video":str(source),"tags":tags}
    finally:
        target.unlink(missing_ok=True)
