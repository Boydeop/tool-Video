"""Optional Vietnamese/Korean Edge online TTS and deterministic FFmpeg mixing."""
from __future__ import annotations
import asyncio
import hashlib
from pathlib import Path
import subprocess
from .fftools import binary

VI_VOICE="vi-VN-HoaiMyNeural"
KO_VOICE="ko-KR-SunHiNeural"

def available():
    try:
        import edge_tts
        return True
    except ImportError:
        return False

async def _speak(text,voice,target):
    import edge_tts
    await edge_tts.Communicate(text,voice).save(str(target))

def synthesize(text,voice,cache_dir):
    if not available():
        raise RuntimeError("Missing edge-tts. Install requirements.txt.")
    cache=Path(cache_dir)
    cache.mkdir(parents=True,exist_ok=True)
    digest=hashlib.sha256((voice+"|"+text).encode("utf-8")).hexdigest()
    target=cache/(digest+".mp3")
    if target.is_file() and target.stat().st_size>100: return target
    tmp=target.with_suffix(".working.mp3")
    try:
        asyncio.run(_speak(text,voice,tmp))
        if not tmp.is_file() or tmp.stat().st_size<=100:
            raise RuntimeError("TTS returned empty audio.")
        tmp.replace(target)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("TTS online failed; video was not rendered with voice: "+type(exc).__name__) from exc
    return target

def prepare_events(questions,cache_dir,think,reveal):
    events=[]
    length=(think+reveal)
    for index,q in enumerate(questions):
        start_ms=round(index*length*1000)
        # Vietnamese question at beginning, Korean pronunciation on reveal.
        vi=synthesize(q.question,VI_VOICE,cache_dir)
        ko=synthesize(q.correct,KO_VOICE,cache_dir)
        events.append((vi,start_ms))
        events.append((ko,start_ms+round(think*1000)))
    return events

def audio_streams(path):
    cmd=[binary("ffprobe"),"-v","error","-select_streams","a",
         "-show_entries","stream=codec_name","-of","csv=p=0",str(path)]
    proc=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                        text=True,timeout=30,check=False)
    if proc.returncode:
        raise RuntimeError("ffprobe failed: "+proc.stderr[-600:])
    return [x.strip() for x in proc.stdout.splitlines() if x.strip()]

def mux_narration(silent_video,events,final_path,duration):
    if not events: raise ValueError("No speech assets to mux")
    cmd=[binary("ffmpeg"),"-hide_banner","-loglevel","error","-y",
         "-i",str(silent_video)]
    filters=[]
    for n,(audio,delay_ms) in enumerate(events,1):
        if not Path(audio).is_file(): raise ValueError("Missing narration clip")
        cmd.extend(["-i",str(audio)])
        filters.append(f"[{n}:a]adelay={int(delay_ms)}:all=1[a{n}]")
    refs="".join(f"[a{n}]" for n in range(1,len(events)+1))
    filters.append(refs+f"amix=inputs={len(events)}:duration=longest:dropout_transition=0,atrim=duration={duration:.3f},apad[audio]")
    cmd+=["-filter_complex",";".join(filters),"-map","0:v:0","-map","[audio]",
          "-c:v","copy","-c:a","aac","-b:a","160k",
          "-t",f"{duration:.3f}","-movflags","+faststart",str(final_path)]
    result=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                          text=True,timeout=max(120,int(duration*10)),check=False)
    if result.returncode:
        Path(final_path).unlink(missing_ok=True)
        raise RuntimeError("Narration mux failed: "+result.stderr[-1400:])
    if not audio_streams(final_path):
        Path(final_path).unlink(missing_ok=True)
        raise RuntimeError("Output MP4 contains no audio stream.")
    return str(final_path)
