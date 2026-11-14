"""v1.1.2 Korean glyph quality and standard MP4 metadata compatibility."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
import os
import shutil
import subprocess
import tempfile
import unittest

from PIL import Image,ImageChops
from studio.renderer import _LogicalDraw,font,draw_frame,_write_center
from studio.quiz import Quiz
from studio.metadata import overwrite_metadata,inspect_metadata
from studio.service import Service
from studio.fftools import hidden_subprocess

def q():
    return Quiz(topic="Giáng sinh ở Hàn Quốc",question="Thiệp Giáng sinh",
                correct="크리스마스 카드",wrong1="교통카드",
                wrong2="신용카드",wrong3="엽서",level="TOPIK 1",
                clue="Thiệp Giáng sinh",meaning="Thiệp Giáng sinh")

def _korean_and_vietnamese_fonts_available():
    try:
        font(24,bold=True,korean=True)
        font(24,bold=True,korean=False)
        return True
    except RuntimeError:
        return False

@unittest.skipUnless(_korean_and_vietnamese_fonts_available(),
                     "System Korean/Vietnamese fonts unavailable in CI runner")
class KoreanFontTests(unittest.TestCase):
    def test_native_font_is_two_times_logical_size(self):
        image=Image.new("RGB",(1080,1920),"white")
        draw=_LogicalDraw(image,2)
        hangul=font(26,bold=True,korean=True)
        self.assertEqual(draw._font(hangul).size,52)
        _write_center(draw,540,400,"한국어 quiz · Chào",34,"#161616")
        self.assertIsNotNone(ImageChops.difference(image,Image.new("RGB",image.size,"white")).getbbox())

    def test_full_hd_not_just_upscaled_540p(self):
        small=draw_frame(q(),1,5,"sejong",4.0,think=5.5,width=540,
                         prompt_duration=1.5,shorts_safe=False)
        high=draw_frame(q(),1,5,"sejong",4.0,think=5.5,width=1080,
                        prompt_duration=1.5,shorts_safe=False)
        self.assertEqual(high.size,(1080,1920))
        bilinear=small.resize(high.size,Image.Resampling.LANCZOS)
        difference=ImageChops.difference(high,bilinear)
        self.assertIsNotNone(difference.getbbox(),
            "1080p must rasterize text natively, not upscale 540p")
        safe=draw_frame(q(),1,5,"sejong",4.0,think=5.5,width=1080,
                        prompt_duration=1.5,shorts_safe=True)
        self.assertEqual(safe.size,high.size)

class WindowsMetadataTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "FFmpeg/FFprobe required")
    def test_standard_ilst_tags_verified_and_legacy_mdta_repaired(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"old.mp4"
            subprocess.run(["ffmpeg","-y","-hide_banner","-loglevel","error",
                "-f","lavfi","-i","color=c=black:s=160x288:d=1",
                "-c:v","libx264","-pix_fmt","yuv420p",str(path)],
                check=True,timeout=30,**hidden_subprocess())
            # Simulate an older v1.1 MP4 containing mdta instead of ilst.
            legacy=Path(td)/"legacy.mp4"
            subprocess.run(["ffmpeg","-y","-hide_banner","-loglevel","error",
                "-i",str(path),"-map","0","-c","copy",
                "-movflags","+faststart+use_metadata_tags",
                "-metadata","title=Old Clip",str(legacy)],
                check=True,timeout=30,**hidden_subprocess())
            self.assertEqual(inspect_metadata(legacy)["layout"],"mdta")
            fields={
                "metadata_title":"Tiếng Hàn So Easy | Giáng sinh",
                "metadata_artist":"Tiếng Hàn So Easy",
                "metadata_copyright":"© 2026 Tiếng Hàn So Easy",
                "metadata_comment":"tienghansoeasy.io.vn",
            }
            result=overwrite_metadata(legacy,fields)
            self.assertTrue(result["verified"])
            info=inspect_metadata(legacy)
            self.assertEqual(info["layout"],"ilst")
            self.assertTrue(info["verified"])
            self.assertEqual(info["tags"]["title"],fields["metadata_title"])
            self.assertEqual(info["tags"]["artist"],fields["metadata_artist"])
            self.assertEqual(info["tags"]["copyright"],fields["metadata_copyright"])
            self.assertEqual(info["tags"]["date"],str(datetime.now(timezone(timedelta(hours=7))).year))
            self.assertIn(b"ilst",legacy.read_bytes())
            self.assertIn(b"cprt",legacy.read_bytes())

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "FFmpeg/FFprobe required")
    def test_missing_required_tag_does_not_replace_video(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"clip.mp4"
            subprocess.run(["ffmpeg","-y","-hide_banner","-loglevel","error",
                "-f","lavfi","-i","color=c=black:s=160x288:d=1",
                "-c:v","libx264","-pix_fmt","yuv420p",str(path)],
                check=True,timeout=30,**hidden_subprocess())
            original=path.read_bytes()
            with self.assertRaises(ValueError):
                overwrite_metadata(path,{"metadata_title":"Test"})
            self.assertEqual(path.read_bytes(),original)

    def test_bulk_repair_skips_scheduled_and_is_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            service=Service(td)
            folder=Path(td)/"videos";folder.mkdir()
            old=folder/"old.mp4";old.write_bytes(b"fake")
            scheduled=folder/"scheduled.mp4";scheduled.write_bytes(b"fake")
            # No real video data required when external metadata actions mocked.
            service.list_videos=lambda limit=20:[
                {"name":"old.mp4","topic":"Giáng sinh ở Hàn Quốc"},
                {"name":"scheduled.mp4","topic":"TOPIK"}]
            with patch.object(service.store,"has_pending_page_job",
                              side_effect=lambda p:p.name=="scheduled.mp4"),\
                 patch.object(service.store,"has_outbound",return_value=False),\
                 patch("studio.metadata.inspect_metadata",
                       return_value={"verified":True,"layout":"mdta"}),\
                 patch("studio.metadata.overwrite_metadata",
                       return_value={"verified":True}) as write:
                result=service.repair_legacy_video_metadata(20)
            self.assertEqual(result["repaired"],["old.mp4"])
            self.assertEqual(len(result["skipped"]),1)
            self.assertEqual(write.call_count,1)
            self.assertIn("Giáng sinh",write.call_args.args[1]["metadata_title"])
            with self.assertRaises(ValueError):service.repair_legacy_video_metadata(21)

if __name__=="__main__":unittest.main()
