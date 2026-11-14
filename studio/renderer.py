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

@lru_cache(maxsize=256)
def _native_face(path,index,size):
    return ImageFont.truetype(path,max(1,int(size)),index=int(index))

class _LogicalDraw:
    """Draw shapes in 540x960 layout coordinates at native output resolution.

    The previous renderer created 540px Hangul and enlarged it to 1080px,
    which could soften or distort strokes. Fonts are now rasterized at actual
    output size. No separate font file is distributed.
    """
    def __init__(self,image,scale):
        self.raw=ImageDraw.Draw(image)
        self.scale=float(scale)

    def _font(self,face):
        return _native_face(face.path,getattr(face,"index",0),
                            round(face.size*self.scale))

    def _box(self,coords):
        return tuple(round(float(v)*self.scale) for v in coords)

    def _points(self,coords):
        return [self._box(pt) for pt in coords]

    def rounded_rectangle(self,xy,radius=0,**kwargs):
        if "width" in kwargs:kwargs["width"]=max(1,round(kwargs["width"]*self.scale))
        return self.raw.rounded_rectangle(self._box(xy),radius=round(radius*self.scale),**kwargs)

    def rectangle(self,xy,**kwargs):
        if "width" in kwargs:kwargs["width"]=max(1,round(kwargs["width"]*self.scale))
        return self.raw.rectangle(self._box(xy),**kwargs)

    def ellipse(self,xy,**kwargs):
        if "width" in kwargs:kwargs["width"]=max(1,round(kwargs["width"]*self.scale))
        return self.raw.ellipse(self._box(xy),**kwargs)

    def line(self,xy,fill=None,width=1,**kwargs):
        return self.raw.line(self._points(xy),fill=fill,
                             width=max(1,round(width*self.scale)),**kwargs)

    def polygon(self,xy,**kwargs):
        return self.raw.polygon(self._points(xy),**kwargs)

    def textlength(self,text,font):
        return self.raw.textlength(text,font=self._font(font))/self.scale

    def textbbox(self,xy,text,font,**kwargs):
        bb=self.raw.textbbox(self._box(xy),text,font=self._font(font),**kwargs)
        return tuple(v/self.scale for v in bb)

    def text(self,xy,text,font,**kwargs):
        return self.raw.text(self._box(xy),text,font=self._font(font),**kwargs)

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
    # Mixed Segoe UI and Malgun Gothic have different top bearings.
    # Use one common baseline so Korean/Vietnamese text doesn't "jump".
    parts=_segments(text)
    width=_measure(d,text,size,weight)
    x=center-width/2
    offset=max((-d.textbbox((0,0),part,font(size,weight,is_ko),anchor="ls")[1]
               for part,is_ko in parts),default=size)
    baseline=y+offset
    for part,is_ko in parts:
        face=font(size,weight,is_ko)
        d.text((x,baseline),part,font=face,fill=color,anchor="ls",
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
    native=img.width/540
    logo.thumbnail((max_width,round(90*native)),Image.Resampling.LANCZOS)
    margin=round(24*native)
    if position=="custom":
        # Coordinates are fractions of the available area (not absolute pixels).
        x=round(max(0,min(1,logo_x))*(img.width-logo.width))
        y=round(max(0,min(1,logo_y))*(img.height-logo.height))
    else:
        x=margin if "left" in position else img.width-logo.width-margin
        y=round(108*native) if position.startswith("top") else img.height-logo.height-round(62*native)
    result=img.convert("RGBA")
    result.alpha_composite(logo,(x,y))
    return result.convert("RGB")


@lru_cache(maxsize=4)
def _korea_background(theme):
    """Low-opacity, original vector motifs, no external assets/licenses/fonts.

    Palace eaves, N Seoul Tower, Korean mugunghwa and hanji brush strokes live
    behind content; styles differ by template without obscuring Hangul glyphs.
    """
    from PIL import ImageColor
    bg,fg,accent=THEMES[theme]
    img=Image.new("RGBA",(540,960),ImageColor.getrgb(bg)+(255,))
    layer=Image.new("RGBA",(540,960),(0,0,0,0))
    art=ImageDraw.Draw(layer,"RGBA")
    base={"hanji":(117,112,76),"blue":(179,215,248),"sejong":(150,123,70)}[theme]
    red={"hanji":(179,74,56),"blue":(240,169,146),"sejong":(190,70,52)}[theme]
    # Hanji brush fibers: varied lengths, visible but no heavy full-frame noise.
    for i in range(24):
        x=(i*97+13)%540
        y=(i*157+42)%960
        art.line([(x,y),(min(539,x+34+(i%5)*19),min(959,y+5))],
                 fill=base+(15,),width=2)
    # Geometric dancheong edge ribbons, outside the primary card typography.
    for yy in (136,190,414,807,933):
        art.line([(8,yy),(19,yy-10),(31,yy)],fill=base+(58,),width=2)
        art.line([(509,yy),(521,yy-10),(533,yy)],fill=base+(58,),width=2)
    # Gyeongbokgung-style curved roof at top right.
    art.polygon([(340,148),(366,132),(388,123),(412,105),(438,116),
                 (464,126),(490,133),(510,150),(500,145),(472,143),
                 (438,135),(411,124),(382,143)],fill=base+(45,))
    art.line([(343,153),(365,142),(411,126),(467,142),(509,154)],
             fill=red+(65,),width=4)
    for xx in (380,410,439,467):
        art.line([(xx,154),(xx,186)],fill=base+(51,),width=4)
    art.line([(363,187),(486,187)],fill=base+(57,),width=4)
    # Silhouette of N Seoul Tower, kept mostly in side/background space.
    art.ellipse((58,615,128,632),fill=base+(44,))
    art.polygon([(79,632),(105,632),(118,870),(65,870)],fill=base+(38,))
    art.rectangle((88,561,97,616),fill=base+(58,))
    art.ellipse((81,555,104,566),fill=red+(45,))
    art.line([(93,547),(93,524)],fill=base+(66,),width=3)
    # Mugunghwa flower: 5 petals plus circular heart.
    cx,cy=456,858
    for n in range(5):
        ang=2*math.pi*n/5-math.pi/2
        px=cx+math.cos(ang)*21
        py=cy+math.sin(ang)*21
        art.ellipse((px-15,py-18,px+15,py+18),fill=red+(49,))
    art.ellipse((cx-9,cy-9,cx+9,cy+9),fill=base+(62,))
    return Image.alpha_composite(img,layer).convert("RGB")

def _panel_pattern(img,theme):
    """Translucent flower/roof silhouette inside the question card.

    Drawn before Korean/Vietnamese text, with very low contrast for legibility.
    """
    bg=Image.new("RGBA",img.size,(0,0,0,0))
    d=_LogicalDraw(bg,img.width/540)
    tint={"hanji":(171,142,100),"blue":(119,168,205),"sejong":(191,141,111)}[theme]
    for cx,cy,r in ((446,266,20),(86,346,13)):
        for n in range(5):
            ang=n*math.tau/5
            px=cx+math.cos(ang)*r
            py=cy+math.sin(ang)*r
            d.ellipse((px-10,py-10,px+10,py+10),fill=tint+(30,))
        d.ellipse((cx-5,cy-5,cx+5,cy+5),fill=tint+(44,))
    return Image.alpha_composite(img.convert("RGBA"),bg).convert("RGB")


def shorts_safe_frame(image,theme):
    """Keep visual text away from UI overlays in YouTube Shorts / Instagram Reels.

    9:16 canvas stays intact; the design is inset from the top, right action
    rail and lower caption/audio controls. Logo is composited afterwards so
    drag-to-position coordinates remain exact.
    """
    bg=THEMES[theme][0]
    width,height=image.size
    unit=width/540
    output=Image.new("RGB",image.size,bg)
    # Keep the existing Shorts-safe inset but render the original text at
    # output resolution BEFORE the final 9% reduction in this step.
    adjusted=image.resize((round(492*unit),round(875*unit)),
                          Image.Resampling.LANCZOS)
    output.paste(adjusted,(round(24*unit),round(33*unit)))
    return output


def draw_intro(theme,width=540,logo_path=None,logo_position="top-right",logo_scale=.15,progress=1,
               logo_x=.82,logo_y=.10,shorts_safe=False,topic="",level=""):
    bg,fg,accent=THEMES[theme]
    im=_korea_background(theme).copy()
    if width!=540:im=im.resize((width,width*16//9),Image.Resampling.LANCZOS)
    d=_LogicalDraw(im,width/540)
    d.rounded_rectangle((36,225,504,630),radius=29,fill="#FFFFFF" if theme!="blue" else "#153E68")
    _label(d,270,302,"TIẾNG HÀN SO EASY",420,1,34,accent,26)
    _label(d,270,380,"THỬ THÁCH TIẾNG HÀN",424,2,36,"#191612" if theme!="blue" else "#FFFFFF",28)
    if topic:
        _label(d,270,481,str(topic)[:70],420,2,30,
               "#13263A" if theme!="blue" else "#FFFFFF",19)
        _label(d,270,552,"Cùng học từ vựng qua 4 đáp án",410,2,24,
               "#324D63" if theme!="blue" else "#D9EBFD",18)
    else:
        _label(d,270,503,"Chọn đáp án đúng trước khi hết giờ!",410,2,25,
               "#324D63" if theme!="blue" else "#D9EBFD",21)
    _label(d,270,727,("한국어 퀴즈 · "+str(level))[:35] if level else "한국어 퀴즈",410,1,41,accent,27)
    if shorts_safe:im=shorts_safe_frame(im,theme)
    im=_logo_overlay(im,logo_path,logo_position,logo_scale,logo_x,logo_y)
    return im

def draw_frame(quiz:Quiz,idx,total,theme,elapsed,think=5.5,width=540,
               reveal=2.8,transition=.8,logo_path=None,
               logo_position="top-right",logo_scale=.15,
               prompt_duration=1.35,answer_duration=None,logo_x=.82,logo_y=.10,shorts_safe=False):
    """Render a frame with three explicit stages: speaking, thinking, reveal."""
    if theme not in THEMES:
        raise ValueError("Unknown video theme")
    bg,fg,accent=THEMES[theme]
    im=_korea_background(theme).copy()
    if width!=540:im=im.resize((width,width*16//9),Image.Resampling.LANCZOS)
    d=_LogicalDraw(im,width/540)
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
    im=_panel_pattern(im,theme)
    d=_LogicalDraw(im,width/540)
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
                video_topic=questions[0].topic if len({q.topic for q in questions})==1 else "Tổng hợp tiếng Hàn"
                video_level=questions[0].level if len({q.level for q in questions})==1 else "TOPIK MIX"
                im=draw_intro(theme,width,logo_path,logo_position,logo_scale,
                              logo_x=logo_x,logo_y=logo_y,shorts_safe=shorts_safe,
                              topic=video_topic,level=video_level)
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
