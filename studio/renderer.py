"""Offline portrait quiz video renderer using Pillow and FFmpeg."""
from pathlib import Path
import random
import shutil
import subprocess
from PIL import Image,ImageDraw,ImageFont
from .quiz import Quiz
from .fftools import binary
from .voice import prepare_events, mux_narration

THEMES={
 "hanji": ("#FDFBF7","#191612","#1E5BBB"),
 "blue": ("#0F4C81","#FFFFFF","#E84A36"),
 "sejong": ("#F7F4EB","#191612","#D13624")
}

def font(size,bold=False):
    paths=[
        "C:/Windows/Fonts/malgunbd.ttf" if bold else "C:/Windows/Fonts/malgun.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    ]
    for path in paths:
        if Path(path).exists(): return ImageFont.truetype(path,size)
    return ImageFont.load_default()

def fit(draw,string,width,size):
    while size>16 and draw.textbbox((0,0),string,font=font(size,True))[2]>width:
        size-=2
    return font(size,True)

def draw_frame(quiz,idx,total,theme,elapsed,think=4,width=540):
    if width!=540:
        base=draw_frame(quiz,idx,total,theme,elapsed,think,width=540)
        return base.resize((width,width*16//9),Image.Resampling.LANCZOS)
    bg,fg,accent=THEMES[theme]
    h=width*16//9
    image=Image.new("RGB",(width,h),bg)
    d=ImageDraw.Draw(image)
    def txt(x,y,s,size,color=None,max_width=None):
        ft=fit(d,s,max_width,size) if max_width else font(size,True)
        d.text((x,y),s,font=ft,fill=color or fg,anchor="mm")
    d.rounded_rectangle((28,45,width-28,99),radius=13,fill=accent)
    txt(width//2,72,"TIẾNG HÀN SO EASY",23,"#FFFFFF",width-75)
    txt(width//2,145,"PHẢN XẠ TIẾNG HÀN  •  한국어 퀴즈",20,max_width=width-35)
    txt(width//2,192,f"CÂU {idx:02d} / {total:02d} · {quiz.level}",21,max_width=width-50)
    d.rounded_rectangle((30,225,width-30,360),radius=18,fill="#FFFFFF")
    txt(width//2,256,"Từ/câu này tiếng Hàn là gì?",20,"#1E5BBB",width-75)
    txt(width//2,311,quiz.question,44,"#191612",width-80)
    revealed=elapsed>=think
    progress=min(1,elapsed/think)
    d.rounded_rectangle((43,381,width-43,394),radius=6,fill="#B6C9D4")
    if progress>0:
        d.rounded_rectangle((43,381,43+int((width-86)*progress),394),radius=6,
            fill="#30B96E" if revealed else "#E84A36")
    answers=[quiz.correct,quiz.wrong1,quiz.wrong2,quiz.wrong3]
    random.Random(idx*719+sum(ord(c) for c in quiz.question)).shuffle(answers)
    for i,answer in enumerate(answers):
        y=417+i*95
        good=revealed and answer==quiz.correct
        d.rounded_rectangle((30,y,width-30,y+78),radius=14,
            fill="#30B96E" if good else "#FFFFFF",
            outline="#159950" if good else "#C9D5DB",width=2)
        d.ellipse((43,y+17,87,y+61),fill="#1D8B50" if good else "#E4ECF0")
        txt(65,y+40,"ABCD"[i],20,"#FFFFFF" if good else "#1E5BBB")
        txt(width//2+25,y+40,answer,34,"#FFFFFF" if good else "#191612",width-157)
    txt(width//2,837,"TIẾNG HÀN KHÓ CÓ TUI LO !!!",23,max_width=width-30)
    txt(width//2,886,"tienghansoeasy.io.vn",20,accent)
    return image

def render(questions:list[Quiz],destination,theme="hanji",width=540,fps=15,
           think=4,reveal=1.5,with_voice=False,voice_cache=None):
    if theme not in THEMES or width not in (540,720,1080) or not questions:
        raise ValueError("Unsupported theme, resolution or empty questions")
    binary("ffmpeg")
    for q in questions: q.validate()
    target=Path(destination).resolve()
    target.parent.mkdir(parents=True,exist_ok=True)
    events=prepare_events(questions,voice_cache or target.parent/".voice_cache",think,reveal) if with_voice else []
    silent_target=target.with_name(target.stem+".silenttmp.mp4") if with_voice else target
    proc=subprocess.Popen([
      binary("ffmpeg"),"-hide_banner","-loglevel","error","-y",
      "-f","rawvideo","-pix_fmt","rgb24","-s",f"{width}x{width*16//9}",
      "-r",str(fps),"-i","-","-an","-c:v","libx264",
      "-preset","ultrafast","-pix_fmt","yuv420p",str(silent_target)
    ],stdin=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        frames=max(1,round((think+reveal)*fps))
        for i,q in enumerate(questions,1):
            for n in range(frames):
                proc.stdin.write(draw_frame(q,i,len(questions),theme,n/fps,think,width).tobytes())
        proc.stdin.close()
        stderr=proc.stderr.read().decode(errors="replace")
        if proc.wait()!=0: raise RuntimeError("FFmpeg error: "+stderr[-1000:])
    except BaseException:
        if proc.stdin and not proc.stdin.closed: proc.stdin.close()
        proc.kill()
        proc.wait()
        silent_target.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise
    finally:
        proc.stderr.close()
    if with_voice:
        try:
            mux_narration(silent_target,events,target,len(questions)*(think+reveal))
        finally:
            silent_target.unlink(missing_ok=True)
    return str(target)
