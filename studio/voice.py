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

from .fftools import binary, hidden_subprocess

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

def audio_duration(path):
    """Length of a real MP3/WAV measured by bundled ffprobe, in seconds."""
    proc=subprocess.run([binary("ffprobe"),"-v","error",
          "-show_entries","format=duration","-of","default=nk=1:nw=1",
          str(path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,
          text=True,timeout=30,check=False,**hidden_subprocess())
    if proc.returncode:
        raise SpeechError("Không xác định được độ dài giọng đọc.")
    try:
        length=float(proc.stdout.strip())
        if not 0.1<length<90:
            raise ValueError("Invalid TTS length")
        return length
    except ValueError as exc:
        raise SpeechError("File giọng đọc không có thời lượng hợp lệ.") from exc

def prepare_events(questions,cache_dir,think,reveal,engine="auto",
                   intro=0,transition=0,fps=24,return_scenes=False,progress=None):
    """Compute narration timeline using each TTS clip's measured duration.

    For compatibility, returns events only unless return_scenes=True.
    """
    from .timeline import build_scenes
    clips=[]
    for position,question in enumerate(questions,1):
        if progress:progress(position-1,len(questions),"Tạo giọng đọc")
        # Speak the short Vietnamese clue, never a verbose grammar template.
        vi=synthesize(question.short_clue,VI_VOICE,cache_dir,engine)
        ko=synthesize(question.correct,KO_VOICE,cache_dir,engine)
        clips.append((vi,ko,audio_duration(vi),audio_duration(ko)))
        if progress:progress(position,len(questions),"Đã tải giọng đọc")
    scenes=build_scenes(len(questions),think,reveal,transition,intro,fps,
             [row[2] for row in clips],[row[3] for row in clips])
    events=[]
    for (vi,ko,vi_length,ko_length),scene in zip(clips,scenes):
        question_start=scene.start+.18
        answer_start=scene.answer_start+.18
        # Countdown starts only after the Vietnamese TTS has finished.
        if question_start+vi_length > scene.countdown_start-.15:
            raise SpeechError("Giọng đọc câu hỏi vượt quá thời gian dành cho phần giới thiệu.")
        events.append((vi,round(question_start*1000),"voice"))
        events.append((ko,round(answer_start*1000),"voice"))
    return (events,scenes) if return_scenes else events

def audio_streams(path):
    proc=subprocess.run([binary("ffprobe"),"-v","error","-select_streams","a",
         "-show_entries","stream=codec_name","-of","csv=p=0",str(path)],
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=30,check=False,**hidden_subprocess())
    if proc.returncode:
        raise RuntimeError("ffprobe failed: "+proc.stderr[-600:])
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]

def mux_narration(silent_video,events,final_path,duration):
    """Normalize each spoken clip, duck SFX, and limit master mix.

    Accepts legacy (path, delay_ms) pairs and new (path, delay_ms, kind).
    Speech target -18 LUFS, SFX gentler, master peak limited to avoid clipping.
    """
    if not events:raise ValueError("No narration or SFX events")
    cmd=[binary("ffmpeg"),"-hide_banner","-loglevel","error","-y","-i",str(silent_video)]
    filters=[]
    for n,event in enumerate(events,1):
        audio,delay_ms=event[0],int(event[1])
        kind=event[2] if len(event)>2 else "voice"
        if kind not in ("voice","sfx") or delay_ms<0 or not Path(audio).is_file():
            raise ValueError("Invalid narration or SFX event")
        cmd.extend(["-i",str(audio)])
        # Per-clip normalization prevents Korean voice being far louder than
        # Vietnamese voice; final limiter prevents mixed peaks.
        process=("loudnorm=I=-18:TP=-2:LRA=8" if kind=="voice"
                 else "volume=0.55")
        filters.append(f"[{n}:a]aresample=48000,{process},"
                       f"aformat=sample_fmts=fltp:channel_layouts=stereo,"
                       f"adelay={delay_ms}:all=1[a{n}]")
    refs="".join(f"[a{n}]" for n in range(1,len(events)+1))
    filters.append(refs+f"amix=inputs={len(events)}:duration=longest:normalize=0,"
                   f"alimiter=limit=0.89,atrim=duration={duration:.4f},apad[audio]")
    cmd+=["-filter_complex",";".join(filters),"-map","0:v:0","-map","[audio]",
          "-c:v","copy","-c:a","aac","-ar","48000","-ac","2","-b:a","160k",
          "-t",f"{duration:.4f}","-movflags","+faststart",str(final_path)]
    try:
        result=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                              text=True,timeout=max(180,int(duration*20)),check=False,**hidden_subprocess())
        if result.returncode:
            raise RuntimeError("Audio normalization/mux failed: "+result.stderr[-1600:])
        if not audio_streams(final_path):
            raise RuntimeError("Output MP4 has no AAC audio stream.")
    except Exception:
        Path(final_path).unlink(missing_ok=True)
        raise
    return str(final_path)
