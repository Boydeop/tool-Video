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
from .fftools import binary, hidden_subprocess
from .quiz import Quiz
from .sfx import build_sfx
from .timeline import build_scenes, duration as timeline_duration
from .romanization import approximate_romanization
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

def _logo_overlay(img,path,position="top-right",scale=.15,logo_x=0.82,logo_y=0.10):
    if not path:return img
    p=Path(path)
    if not p.is_file():return img
    with Image.open(p) as file:
        logo=file.convert("RGBA")
    max_width=max(28,int(img.width*scale))
    logo.thumbnail((max_width,90),Image.Resampling.LANCZOS)
    margin=24
    if position=="custom":
        # Coordinates are fractions of the available area (not absolute pixels).
        x=round(max(0,min(1,logo_x))*(img.width-logo.width))
        y=round(max(0,min(1,logo_y))*(img.height-logo.height))
    else:
        x=margin if "left" in position else img.width-logo.width-margin
        y=108 if position.startswith("top") else img.height-logo.height-62
    result=img.convert("RGBA")
    result.alpha_composite(logo,(x,y))
    return result.convert("RGB")

@lru_cache(maxsize=6)
def korean_background(theme):
    """Procedural, low-opacity Korean cultural illustrations. No external assets.

    Hanok eaves, traditional window lattice, mountain contour, mugunghwa-like
    five-petal rosette and taegeuk circle. Kept underneath all text/cards.
    """
    background=Image.new("RGBA",(540,960),THEMES[theme][0])
    layer=Image.new("RGBA",(540,960),(0,0,0,0))
    d=ImageDraw.Draw(layer)
    ink=(36,88,155,22) if theme!="blue" else (224,237,255,24)
    warm=(212,63,38,23) if theme!="blue" else (255,230,216,33)
    # Traditional Hanok eaves: a graceful curved roofline and supporting beams.
    d.arc((-86,-40,335,234),170,350,fill=ink,width=6)
    d.line([(2,134),(120,103),(241,126),(328,109),(423,148)],
           fill=ink,width=5,joint="curve")
    d.line([(66,145),(66,186),(286,186),(286,146)],fill=ink,width=3)
    # Traditional lattice window at edges, barely visible behind the cards.
    for x in (8,500):
        d.rectangle((x,300,x+28,500),outline=ink,width=3)
        for y in range(320,498,29):
            d.line((x,y,x+28,y),fill=ink,width=2)
        d.line((x+14,300,x+14,500),fill=ink,width=2)
    # Mountain line, inspired by Korean ink landscapes.
    d.line([(0,872),(80,826),(119,850),(199,794),(274,835),
            (350,784),(443,834),(540,792)],fill=ink,width=5,joint="curve")
    d.line([(0,892),(122,864),(217,885),(351,845),(540,870)],
           fill=ink,width=3,joint="curve")
    # Five petal mugunghwa-like motif.
    import math as _math
    cx,cy=450,180
    for n in range(5):
        theta=2*_math.pi*n/5
        px=cx+36*_math.cos(theta)
        py=cy+36*_math.sin(theta)
        d.ellipse((px-26,py-26,px+26,py+26),outline=warm,width=4)
    d.ellipse((cx-10,cy-10,cx+10,cy+10),fill=warm)
    # Faint taegeuk, a cultural motif rather than an intrusive flag.
    d.arc((407,680,517,790),8,352,fill=ink,width=6)
    d.arc((427,684,486,752),180,360,fill=warm,width=5)
    d.arc((427,726,486,794),0,180,fill=ink,width=5)
    background=Image.alpha_composite(background,layer)
    return background.convert("RGB")

def shorts_safe_frame(image,theme):
    """Balanced 9:16 safe composition with full-frame themed background.

    The old (421,749) content at (20,63) created a giant empty lower/right
    block. Use a centered, readable inset with modest right action-rail room.
    """
    output=korean_background(theme).copy()
    shrunk=image.resize((462,821),Image.Resampling.LANCZOS)
    output.paste(shrunk,(27,58))
    return output


def draw_intro(theme,width=540,logo_path=None,logo_position="top-right",logo_scale=.15,progress=1,
               logo_x=.82,logo_y=.10,shorts_safe=False):
    bg,fg,accent=THEMES[theme]
    im=korean_background(theme).copy();d=ImageDraw.Draw(im)
    d.rounded_rectangle((36,225,504,630),radius=29,fill="#FFFFFF" if theme!="blue" else "#153E68")
    _label(d,270,302,"TIẾNG HÀN SO EASY",420,1,34,accent,26)
    _label(d,270,380,"THỬ THÁCH TIẾNG HÀN",424,2,36,"#191612" if theme!="blue" else "#FFFFFF",28)
    _label(d,270,503,"Chọn đáp án đúng trước khi hết giờ!",410,2,25,"#324D63" if theme!="blue" else "#D9EBFD",21)
    _label(d,270,727,"한국어 퀴즈",410,1,41,accent,30)
    if shorts_safe:im=shorts_safe_frame(im,theme)
    im=_logo_overlay(im,logo_path,logo_position,logo_scale,logo_x,logo_y)
    if width!=540:im=im.resize((width,width*16//9),Image.Resampling.LANCZOS)
    return im

def draw_frame(quiz:Quiz,idx,total,theme,elapsed,think=5.5,width=540,
               reveal=2.8,transition=.8,logo_path=None,
               logo_position="top-right",logo_scale=.15,
               prompt_duration=1.35,answer_duration=None,logo_x=.82,logo_y=.10,shorts_safe=False):
    """Render a frame with three explicit stages: speaking, thinking, reveal."""
    if theme not in THEMES:
        raise ValueError("Unknown video theme")
    if width!=540:
        frame=draw_frame(quiz,idx,total,theme,elapsed,think,540,
                         reveal,transition,logo_path,logo_position,logo_scale,
                         prompt_duration,answer_duration,logo_x,logo_y,shorts_safe)
        return frame.resize((width,width*16//9),Image.Resampling.LANCZOS)

    bg,fg,accent=THEMES[theme]
    im=korean_background(theme).copy()
    d=ImageDraw.Draw(im)
    answer_start=prompt_duration+think
    show_answer=elapsed>=answer_start
    countdown=prompt_duration<=elapsed<answer_start
    seconds_remaining=max(0,math.ceil(think-(elapsed-prompt_duration)))
    answer_duration=answer_duration if answer_duration is not None else reveal

    d.rounded_rectangle((28,35,512,96),radius=18,fill=accent)
    _label(d,270,54,"TIẾNG HÀN SO EASY",425,1,26,"#FFFFFF",22)
    _label(d,270,119,"TỪ VỰNG TIẾNG HÀN · 한국어 퀴즈",455,1,22,fg,19)
    _label(d,270,164,f"CÂU {idx:02d} / {total:02d} · {quiz.level}",460,1,20,fg,18)

    # Very short prompt, including for legacy questions.
    d.rounded_rectangle((27,207,513,382),radius=21,fill="#FFFFFF")
    _label(d,270,222,"TIẾNG HÀN LÀ GÌ?",430,1,22,accent,19)
    clue=quiz.short_clue
    size,lines=_fitted(d,clue,438,3,45,24)
    height=size+9
    start=288-(len(lines)-1)*height*.48
    for i,line in enumerate(lines):
        _write_center(d,270,start+i*height,line,size,"#191612")

    if elapsed<prompt_duration:
        hint="ĐANG NGHE CÂU HỎI"
        timer_color=accent
        progress=0.0
    elif countdown:
        hint=f"CHỌN ĐÁP ÁN · {seconds_remaining}s"
        timer_color="#D13624"
        progress=min(1,max(0,(elapsed-prompt_duration)/think))
    else:
        hint="ĐÁP ÁN ĐÚNG"
        timer_color="#178451"
        progress=1.0
    _label(d,270,400,hint,470,1,21,timer_color,18)
    d.rounded_rectangle((35,439,505,451),radius=6,fill="#CDD7DF")
    if progress>.001:
        d.rounded_rectangle((35,439,35+int(470*progress),451),
                            radius=6,fill=timer_color)

    choices=[quiz.correct,quiz.wrong1,quiz.wrong2,quiz.wrong3]
    random.Random(idx*719+sum(ord(c) for c in quiz.question)).shuffle(choices)
    for n,option in enumerate(choices):
        top=463+n*88
        correct=show_answer and option==quiz.correct
        d.rounded_rectangle((28,top,512,top+82),radius=14,
            fill="#179662" if correct else "#FFFFFF",
            outline="#0F7F51" if correct else "#D0DADF",width=2)
        d.ellipse((42,top+19,84,top+61),fill="#11744D" if correct else "#E7EFF3")
        _write_center(d,63,top+27,"ABCD"[n],19,"#FFFFFF" if correct else "#0F4C81")
        if show_answer:
            details=quiz.answer_details(option)
            romanization=details["romanization"].strip()
            meaning=details["meaning"].strip()
            _label(d,285,top+4,option,386,1,29,
                   "#FFFFFF" if correct else "#191612",21)
            _label(d,285,top+35,romanization or "Phiên âm: chưa có",
                   386,1,16,"#E3FFEF" if correct else "#3B6074",13)
            _label(d,285,top+57,meaning or "Nghĩa: cần bổ sung",
                   390,1,17,"#FFFFFF" if correct else "#28435A",13)
        else:
            _label(d,285,top+24,option,386,1,33,"#191612",22)
        if correct:
            d.rounded_rectangle((29,top+1,511,top+81),radius=14,
                                outline="#A7EFCC",width=3)

    # Explanation appears only AFTER the timer, never while choosing an answer.
    if show_answer:
        d.rounded_rectangle((28,824,512,913),radius=14,
                            fill="#E9F8EF" if theme!="blue" else "#F2FAFF")
        meaning=quiz.vietnamese_meaning
        _label(d,270,834,"NGHĨA TIẾNG VIỆT",450,1,16,"#1E5BBB",15)
        _label(d,270,860,meaning if meaning else "Chưa có nghĩa được kiểm chứng",
               441,2,25,"#191612",18,3)
        if not quiz.romanization.strip() and approximate_romanization(quiz.correct):
            _label(d,270,896,"Phiên âm trên là gợi ý, cần kiểm tra lại",455,1,13,
                   "#617582",12)
    else:
        _label(d,270,860,"NGHE XONG, CHỌN ĐÁP ÁN",463,1,19,fg,16)

    _label(d,270,929,"tienghansoeasy.io.vn",470,1,17,accent,15)
    if shorts_safe:im=shorts_safe_frame(im,theme)
    im=_logo_overlay(im,logo_path,logo_position,logo_scale,logo_x,logo_y)

    # Soft transition; no hard flashes or blurred Hangul.
    fade_in=min(1,max(0,elapsed/.5))
    end=prompt_duration+think+answer_duration
    fade_out=min(1,max(0,(end+transition-elapsed)/transition))
    alpha=max(0,min(fade_in,fade_out))
    if alpha<1:
        underlying=Image.new("RGB",im.size,bg)
        im=Image.blend(underlying,im,alpha)
    return im


def render(questions:list[Quiz],destination,theme="hanji",width=540,fps=24,
           think=5.5,reveal=2.8,with_voice=False,voice_cache=None,
           voice_engine="auto",transition=.8,intro=1.5,sfx=True,sfx_volume=.34,
           logo_path=None,logo_position="top-right",logo_scale=.15,
           logo_x=.82,logo_y=.10,progress=None,shorts_safe=False):
    if not questions or theme not in THEMES or width not in (540,1080) or fps not in (24,30):
        raise ValueError("Unsupported video configuration.")
    if not 4<=think<=12 or not 1.5<=reveal<=7 or not .3<=transition<=2 or not .5<=intro<=4:
        raise ValueError("Video timing outside allowed limits.")
    binary("ffmpeg")
    def report(p,stage):
        if progress:progress(min(98,max(0,int(p))),stage)
    report(1,"Chuẩn bị dữ liệu")
    for quiz in questions:
        quiz.validate()
    target=Path(destination).resolve()
    target.parent.mkdir(parents=True,exist_ok=True)
    cache=Path(voice_cache or target.parent/".voice_cache")
    cache.mkdir(parents=True,exist_ok=True)

    # Only TTS input tells us real speech length. A silent preview uses a
    # brief fixed 'reading' stage followed by the same full countdown.
    if with_voice:
        voice_events,scenes=prepare_events(
            questions,cache,think,reveal,engine=voice_engine,
            intro=intro,transition=transition,fps=fps,return_scenes=True,
            progress=lambda current,total,phase:report(3+17*current/max(1,total),phase))
    else:
        scenes=build_scenes(len(questions),think,reveal,transition,intro,fps)
        voice_events=[]
    report(22,"Chuẩn bị khung hình và âm thanh")
    duration=timeline_duration(scenes)
    with tempfile.TemporaryDirectory(prefix="thse_render_") as td:
        folder=Path(td)
        events=list(voice_events)
        if sfx:
            track=build_sfx(folder/"sfx.wav",len(questions),think,reveal,
                            transition,intro,sfx_volume,scenes=scenes)
            events.append((track,0,"sfx"))
        silent=folder/"silent.mp4"
        proc=subprocess.Popen([
            binary("ffmpeg"),"-hide_banner","-loglevel","error","-y",
            "-f","rawvideo","-pix_fmt","rgb24","-s",f"{width}x{width*16//9}",
            "-r",str(fps),"-i","-","-an","-c:v","libx264","-preset","veryfast",
            "-pix_fmt","yuv420p","-movflags","+faststart",str(silent)
        ],stdin=subprocess.PIPE,stderr=subprocess.PIPE,**hidden_subprocess())
        try:
            frames_total=int(round(scenes[0].start*fps))+sum(int(round(scene.duration*fps)) for scene in scenes)
            frames_done=0
            step=max(1,frames_total//75)
            for tick in range(int(round(scenes[0].start*fps))):
                im=draw_intro(theme,width,logo_path,logo_position,logo_scale,
                              logo_x=logo_x,logo_y=logo_y,shorts_safe=shorts_safe)
                proc.stdin.write(im.tobytes())
                frames_done+=1
                if frames_done%step==0:report(24+65*frames_done/frames_total,"Đang vẽ khung video")
            for i,(q,scene) in enumerate(zip(questions,scenes),1):
                frames=int(round(scene.duration*fps))
                for tick in range(frames):
                    im=draw_frame(q,i,len(questions),theme,tick/fps,think,width,
                                  reveal,transition,logo_path,logo_position,logo_scale,
                                  prompt_duration=scene.prompt_duration,
                                  answer_duration=scene.answer_duration,
                                  logo_x=logo_x,logo_y=logo_y,shorts_safe=shorts_safe)
                    proc.stdin.write(im.tobytes())
                    frames_done+=1
                    if frames_done%step==0:report(24+65*frames_done/frames_total,
                                                 "Đang dựng câu "+str(i)+"/"+str(len(questions)))
            proc.stdin.close()
            stderr=proc.stderr.read().decode("utf-8","replace")
            if proc.wait()!=0:
                raise RuntimeError("FFmpeg render failed: "+stderr[-1400:])
        except BaseException:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
            proc.kill();proc.wait()
            raise
        finally:
            proc.stderr.close()
        report(90,"Đang ghép âm thanh")
        if events:
            mux_narration(silent,events,target,duration)
        else:
            # Even silent export must contain an AAC stereo stream for Reels.
            command=[binary("ffmpeg"),"-hide_banner","-loglevel","error","-y",
                    "-i",str(silent),"-f","lavfi","-i",
                    "anullsrc=channel_layout=stereo:sample_rate=48000",
                    "-map","0:v:0","-map","1:a:0","-c:v","copy",
                    "-c:a","aac","-b:a","128k","-t",f"{duration:.4f}",
                    "-movflags","+faststart",str(target)]
            result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                  timeout=max(90,int(duration*10)),check=False,**hidden_subprocess())
            if result.returncode:
                raise RuntimeError("Silent AAC mux failed: "+
                                   result.stderr.decode("utf-8","replace")[-500:])
        report(98,"Đã dựng MP4, đang ghi metadata")
    return str(target)
