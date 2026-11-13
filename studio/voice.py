"""Resilient online Vietnamese/Korean narration with explicit TTS fallback.

Engine modes: auto (Edge first, gTTS fallback), edge, google. Both depend on
third-party online services and may fail. Never silently return a mute video.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
import subprocess
import time

from .fftools import binary

LOGGER=logging.getLogger("tienghansoeasy.voice")
VOICES={
    "vi":("vi-VN-HoaiMyNeural","vi-VN-NamMinhNeural"),
    "ko":("ko-KR-SunHiNeural","ko-KR-InJoonNeural"),
}
LANGUAGES={"vi":"vi","ko":"ko"}
VI_VOICE=VOICES["vi"][0]
KO_VOICE=VOICES["ko"][0]
ENGINES=("auto","edge","google")

class SpeechError(RuntimeError):
    pass

def available():
    return readiness()["edge"] or readiness()["google"]

def readiness():
    try: import edge_tts
    except ImportError: edge=False
    else: edge=True
    try: import gtts
    except ImportError: google=False
    else: google=True
    return {"edge":edge,"google":google}

def check_engine(engine):
    if engine not in ENGINES:
        raise ValueError("Nguồn giọng đọc không hợp lệ.")
    return engine

async def _speak_edge(text,voice,target):
    import edge_tts
    await edge_tts.Communicate(text,voice).save(str(target))

def _speak_google(text,language,target):
    from gtts import gTTS
    gTTS(text=text,lang=language,slow=False,timeout=20).save(str(target))

def synthesize(text,voice,cache_dir,engine="auto"):
    """Return an MP3 file. Only cache successful files per TTS engine/voice/text."""
    check_engine(engine)
    if not isinstance(text,str) or not text.strip() or len(text)>500:
        raise ValueError("Nội dung TTS phải từ 1 đến 500 ký tự.")
    language=next((code for code,voices in VOICES.items() if voice in voices),None)
    if language is None:
        raise ValueError("Giọng đọc không được hỗ trợ.")
    cache=Path(cache_dir)
    cache.mkdir(parents=True,exist_ok=True)
    ready=readiness()
    engines=(("edge","google") if engine=="auto" else (engine,))
    errors=[]
    for provider in engines:
        if not ready.get(provider,False):
            errors.append(provider+": chưa cài thư viện")
            continue
        voices=VOICES[language] if provider=="edge" else (language,)
        for name in voices:
            digest=hashlib.sha256((provider+"|"+name+"|"+text).encode("utf-8")).hexdigest()
            target=cache/(digest+".mp3")
            if target.is_file() and target.stat().st_size>100:
                return target
            # Retry transient Edge NoAudioReceived, then try next voice/provider.
            tries=2 if provider=="edge" else 1
            for attempt in range(tries):
                tmp=cache/(digest+".working.mp3")
                tmp.unlink(missing_ok=True)
                try:
                    if provider=="edge":
                        asyncio.run(_speak_edge(text,name,tmp))
                    else:
                        _speak_google(text,language,tmp)
                    if not tmp.is_file() or tmp.stat().st_size<=100:
                        raise SpeechError("TTS không trả dữ liệu âm thanh.")
                    tmp.replace(target)
                    LOGGER.info("TTS generated via %s for %s (%s bytes)",provider,language,target.stat().st_size)
                    return target
                except Exception as exc:
                    tmp.unlink(missing_ok=True)
                    # Never log text or complete URL; error type is enough.
                    LOGGER.warning("TTS provider=%s voice=%s attempt=%s error=%s",
                                   provider,name,attempt+1,type(exc).__name__)
                    errors.append(provider+" "+name+": "+type(exc).__name__)
                    if attempt+1<tries:
                        time.sleep(0.7*(attempt+1))
    raise SpeechError(
        "Không nhận được âm thanh từ các dịch vụ TTS. "
        +"; ".join(errors[-5:])
        +". Thử nguồn Google TTS hoặc chọn xuất không giọng đọc.")

def test_speech(language,engine,cache_dir):
    if language not in LANGUAGES:
        raise ValueError("Ngôn ngữ không hợp lệ.")
    phrase={"vi":"Xin chào! Cùng học tiếng Hàn mỗi ngày.",
            "ko":"안녕하세요. 한국어를 같이 공부해요."}[language]
    path=synthesize(phrase,VOICES[language][0],cache_dir,engine)
    return path

def prepare_events(questions,cache_dir,think,reveal,engine="auto"):
    events=[]
    duration=think+reveal
    for index,q in enumerate(questions):
        offset=round(index*duration*1000)
        vi=synthesize(q.question,VI_VOICE,cache_dir,engine)
        ko=synthesize(q.correct,KO_VOICE,cache_dir,engine)
        events.append((vi,offset))
        events.append((ko,offset+round(think*1000)))
    return events

def audio_streams(path):
    proc=subprocess.run([binary("ffprobe"),"-v","error","-select_streams","a",
         "-show_entries","stream=codec_name","-of","csv=p=0",str(path)],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=30,check=False)
    if proc.returncode:
        raise RuntimeError("ffprobe failed: "+proc.stderr[-600:])
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]

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
