"""Portrait quiz renderer: Unicode-safe mixed scripts, readable layout and motion."""
from __future__ import annotations
from functools import lru_cache
import io
import math
from pathlib import Path
import random
import subprocess
import tempfile
import unicodedata

from PIL import Image,ImageDraw,ImageFont
from .fftools import binary
from .quiz import Quiz
from .sfx import build_sfx
from .voice import prepare_events,mux_narration

THEMES={
    "hanji":("#FDFBF7","#191612","#1E5BBB"),
    "blue":("#0F4C81","#FFFFFF","#E84A36"),
    "sejong":("#F7F4EB","#191612","#D13624")
}
KOREAN_PATHS=[
    "C:/Windows/Fonts/malgun.ttf","C:/Windows/Fonts/malgunbd.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"
]
VIETNAMESE_REGULAR=[
    "C:/Windows/Fonts/segoeui.ttf","C:/Windows/Fonts/arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]
VIETNAMESE_BOLD=[
    "C:/Windows/Fonts/segoeuib.ttf","C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]
KOREAN_BOLD=[
    "C:/Windows/Fonts/malgunbd.ttf","C:/Windows/Fonts/malgun.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
]

@lru_cache(maxsize=128)
def font(size,bold=False,korean=False):
    paths=(KOREAN_BOLD if bold else KOREAN_PATHS) if korean else (VIETNAMESE_BOLD if bold else VIETNAMESE_REGULAR)
    for name in paths:
        if Path(name).is_file():
            return ImageFont.truetype(name,int(size))
    raise RuntimeError("Không có font tiếng Việt/Hàn tương thích. Cần Windows Segoe UI + Malgun Gothic hoặc Noto Sans CJK.")

def _is_hangul(c):
    return ("\uac00"<=c<="\ud7a3" or "\u1100"<=c<="\u11ff" or "\u3130"<=c<="\u318f")

def _segments(text):
    text=unicodedata.normalize("NFC",str(text))
    if not text:return []
    chunks=[];part=text[0];mode=_is_hangul(text[0])
    for char in text[1:]:
        val=_is_hangul(char)
        if val!=mode:
            chunks.append((part,mode))
            part=char;mode=val
        else:part+=char
    chunks.append((part,mode))
    return chunks

def _measure(d,text,size,bold=True):
    return sum(d.textlength(part,font(size,bold,mode)) for part,mode in _segments(text))

def _wrap(d,text,size,limit,max_lines):
    lines=[];current=""
    for word in str(text).split():
        trial=(current+" "+word).strip()
        if _measure(d,trial,size)<=limit:
            current=trial;continue
        if current:
            lines.append(current);current=""
        if _measure(d,word,size)<=limit:
            current=word;continue
        # Break long no-space Korean tokens by grapheme-like syllable.
        for ch in word:
            trial=current+ch
            if current and _measure(d,trial,size)>limit:
                lines.append(current);current=ch
            else:current=trial
    if current:lines.append(current)
    if len(lines)>max_lines:
        lines=lines[:max_lines]
        end=lines[-1]
        while end and _measure(d,end+"…",size)>limit:
            end=end[:-1]
        lines[-1]=end+"…"
    return lines or [""]

def _fitted(d,text,limit,lines=3,initial=34,minimum=23):
    for s in range(initial,minimum-1,-1):
        wrapped=_wrap(d,text,s,limit,lines)
        if not wrapped[-1].endswith("…"):
            return s,wrapped
    return minimum,_wrap(d,text,minimum,limit,lines)

def _write_center(d,center,y,text,size,color,weight=True):
    width=_measure(d,text,size,weight)
    x=center-width/2
    for part,is_ko in _segments(text):
        face=font(size,weight,is_ko)
        d.text((x,y),part,font=face,fill=color,anchor="lt",
               stroke_width=0)
        x+=d.textlength(part,face)

def _label(d,cx,top,text,max_width,max_lines,size,color,minimum=22,line_gap=7):
    font_size,lines=_fitted(d,text,max_width,max_lines,size,minimum)
    height=font_size+line_gap
    for i,line in enumerate(lines):
        _write_center(d,cx,top+i*height,line,font_size,color)
    return len(lines)*height

def _logo_overlay(img,path,position="top-right",scale=.15):
    if not path:return img
    p=Path(path)
    if not p.is_file():return img
    with Image.open(p) as file:
        logo=file.convert("RGBA")
    max_width=max(28,int(img.width*scale))
    logo.thumbnail((max_width,90),Image.Resampling.LANCZOS)
    margin=24
    x=margin if "left" in position else img.width-logo.width-margin
    y=108 if position.startswith("top") else img.height-logo.height-62
    result=img.convert("RGBA")
    result.alpha_composite(logo,(x,y))
    return result.convert("RGB")

def draw_intro(theme,width=540,logo_path=None,logo_position="top-right",logo_scale=.15,progress=1):
    bg,fg,accent=THEMES[theme]
    im=Image.new("RGB",(540,960),bg);d=ImageDraw.Draw(im)
    d.rounded_rectangle((36,225,504,630),radius=29,fill="#FFFFFF" if theme!="blue" else "#153E68")
    _label(d,270,302,"TIẾNG HÀN SO EASY",420,1,34,accent,26)
    _label(d,270,380,"THỬ THÁCH TIẾNG HÀN",424,2,36,"#191612" if theme!="blue" else "#FFFFFF",28)
    _label(d,270,503,"Chọn đáp án đúng trước khi hết giờ!",410,2,25,"#324D63" if theme!="blue" else "#D9EBFD",21)
    _label(d,270,727,"한국어 퀴즈",410,1,41,accent,30)
    im=_logo_overlay(im,logo_path,logo_position,logo_scale)
    if width!=540:im=im.resize((width,width*16//9),Image.Resampling.LANCZOS)
    return im

def draw_frame(quiz:Quiz,idx,total,theme,elapsed,think=5.5,width=540,
               reveal=2.8,transition=.8,logo_path=None,
               logo_position="top-right",logo_scale=.15):
    if theme not in THEMES:raise ValueError("Unknown video template.")
    if width!=540:
        image=draw_frame(quiz,idx,total,theme,elapsed,think,540,
                         reveal,transition,logo_path,logo_position,logo_scale)
        return image.resize((width,width*16//9),Image.Resampling.LANCZOS)
    bg,fg,accent=THEMES[theme]
    im=Image.new("RGB",(540,960),bg);d=ImageDraw.Draw(im)
    # Brand bar with existing CSS palette; leave margin for logos.
    d.rounded_rectangle((28,41,512,101),radius=18,fill=accent)
    _label(d,270,61,"TIẾNG HÀN SO EASY",430,1,26,"#FFFFFF",22)
    _label(d,270,135,"PHẢN XẠ TIẾNG HÀN · 한국어 퀴즈",455,1,22,fg,19)
    _label(d,270,181,f"CÂU {idx:02d} / {total:02d} · {quiz.level}",460,1,20,fg,18)
    d.rounded_rectangle((27,224,513,398),radius=20,fill="#FFFFFF")
    _label(d,270,238,"Từ/câu này tiếng Hàn là gì?",432,1,22,"#1E5BBB",19)
    font_size,lines=_fitted(d,quiz.question,442,3,35,23)
    line_height=font_size+10
    start=310-max(0,len(lines)-1)*line_height/2
    for line_num,line in enumerate(lines):
        _write_center(d,270,start+line_num*line_height,line,font_size,"#191612")
    shown=elapsed>=think
    remaining=max(0,math.ceil(think-elapsed))
    _label(d,270,411,"ĐÁP ÁN SAU: "+str(remaining)+"s" if not shown else "ĐÁP ÁN CHÍNH XÁC ✓",
           465,1,19,"#1E5BBB" if not shown else "#167B46",17)
    progress=max(.001,min(1,elapsed/think))
    d.rounded_rectangle((34,446,506,458),radius=7,fill="#C3D3DD")
    d.rounded_rectangle((34,446,34+int(472*progress),458),radius=7,
                         fill="#2FA968" if shown else "#E84A36")
    opts=[quiz.correct,quiz.wrong1,quiz.wrong2,quiz.wrong3]
    random.Random(idx*719+sum(map(ord,quiz.question))).shuffle(opts)
    for i,answer in enumerate(opts):
        top=478+i*91
        correct=shown and answer==quiz.correct
        d.rounded_rectangle((28,top,512,top+77),radius=15,
             fill="#209C65" if correct else "#FFFFFF",
             outline="#147F52" if correct else "#CFDAE1",width=2)
        d.ellipse((43,top+17,86,top+60),fill="#12744A" if correct else "#E9F1F5")
        _write_center(d,65,top+25,"ABCD"[i],20,"#FFFFFF" if correct else "#0F4C81")
        _label(d,285,top+20,answer,395,2,31,"#FFFFFF" if correct else "#191612",22,4)
        if correct:
            # A short answer emphasis pulse, no flashing.
            intensity=int(3+3*max(0,1-abs((elapsed-think)-.4)))
            d.rounded_rectangle((29,top+1,511,top+76),radius=15,outline="#A9F2CF",width=intensity)
    _label(d,270,870,"TIẾNG HÀN KHÓ CÓ TUI LO !!!",481,1,22,fg,19)
    _label(d,270,911,"tienghansoeasy.io.vn",440,1,19,accent,18)
    im=_logo_overlay(im,logo_path,logo_position,logo_scale)
    # Smooth 0.6s fade/slide into each new question, and fade out at transition.
    fade_in=min(1,max(0,elapsed/.65))
    end=think+reveal
    fade_out=min(1,max(0,(end+transition-elapsed)/transition))
    alpha=max(0,min(fade_in,fade_out))
    if alpha<1:
        under=Image.new("RGB",im.size,bg)
        shift=int(20*(1-fade_in))
        if shift>0:
            moved=under.copy();moved.paste(im,(0,shift));im=moved
        im=Image.blend(under,im,alpha)
    return im

def render(questions:list[Quiz],destination,theme="hanji",width=540,fps=24,
           think=5.5,reveal=2.8,with_voice=False,voice_cache=None,
           voice_engine="auto",transition=.8,intro=1.5,sfx=True,sfx_volume=.34,
           logo_path=None,logo_position="top-right",logo_scale=.15):
    if not questions or theme not in THEMES or width not in (540,1080) or fps not in (24,30):
        raise ValueError("Unsupported video configuration.")
    if not 4<=think<=12 or not 1.5<=reveal<=7 or not .3<=transition<=2 or not .5<=intro<=4:
        raise ValueError("Video timing outside allowed limits.")
    binary("ffmpeg")
    for quiz in questions:quiz.validate()
    target=Path(destination).resolve()
    target.parent.mkdir(parents=True,exist_ok=True)
    cycle=think+reveal+transition
    duration=intro+len(questions)*cycle
    cache=Path(voice_cache or target.parent/".voice_cache")
    cache.mkdir(parents=True,exist_ok=True)
    events=(prepare_events(questions,cache,think,reveal,engine=voice_engine,
                            intro=intro,transition=transition) if with_voice else [])
    with tempfile.TemporaryDirectory(prefix="thse_render_") as td:
        folder=Path(td)
        if sfx:
            track=build_sfx(folder/"sfx.wav",len(questions),think,reveal,transition,intro,sfx_volume)
            events.append((track,0,"sfx"))
        silent=folder/"silent.mp4"
        proc=subprocess.Popen([
            binary("ffmpeg"),"-hide_banner","-loglevel","error","-y",
            "-f","rawvideo","-pix_fmt","rgb24","-s",f"{width}x{width*16//9}",
            "-r",str(fps),"-i","-","-an","-c:v","libx264","-preset","veryfast",
            "-pix_fmt","yuv420p","-movflags","+faststart",str(silent)
        ],stdin=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            for tick in range(int(round(intro*fps))):
                im=draw_intro(theme,width,logo_path,logo_position,logo_scale)
                proc.stdin.write(im.tobytes())
            total_frames=int(round(cycle*fps))
            for i,q in enumerate(questions,1):
                for tick in range(total_frames):
                    im=draw_frame(q,i,len(questions),theme,tick/fps,think,width,reveal,
                                  transition,logo_path,logo_position,logo_scale)
                    proc.stdin.write(im.tobytes())
            proc.stdin.close()
            stderr=proc.stderr.read().decode("utf-8","replace")
            if proc.wait()!=0:raise RuntimeError("FFmpeg render failed: "+stderr[-1400:])
        except BaseException:
            if proc.stdin and not proc.stdin.closed:proc.stdin.close()
            proc.kill();proc.wait()
            raise
        finally:proc.stderr.close()
        if events:
            mux_narration(silent,events,target,duration)
        else:
            silent.replace(target)
    return str(target)
