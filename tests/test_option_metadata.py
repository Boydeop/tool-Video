"""Four-choice display, metadata, custom logo and nonblocking export regressions."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from PIL import Image
from studio.quiz import Quiz
from studio.database import Store
from studio.production import Production
from studio.exports import ExportQueue
from studio.metadata import overwrite_metadata
from studio.renderer import _logo_overlay
from studio.fftools import hidden_subprocess
from studio.answer_enrichment import suggest

def question():
    return Quiz(topic="Đồ uống",question="Nước",correct="물",
                wrong1="맥주",wrong2="콜라",wrong3="차",
                romanization="mul",meaning="Nước",
                wrong1_romanization="maekju",wrong1_meaning="Bia",
                wrong2_romanization="kolla",wrong2_meaning="Nước ngọt cola",
                wrong3_romanization="cha",wrong3_meaning="Trà")

class OptionsTests(unittest.TestCase):
    def test_four_metadata_pairs_survive_parse(self):
        q=Quiz.parse(question().to_dict())
        self.assertEqual(q.answer_details("물")["meaning"],"Nước")
        self.assertEqual(q.answer_details("맥주")["romanization"],"maekju")
        self.assertEqual(q.answer_details("콜라")["meaning"],"Nước ngọt cola")
        self.assertEqual(q.answer_details("차")["romanization"],"cha")
        self.assertEqual(q.missing_answer_details,[])
        legacy=q.to_dict()
        for field in ("wrong1_meaning","wrong1_romanization","wrong2_meaning",
                      "wrong2_romanization","wrong3_meaning","wrong3_romanization"):
            legacy.pop(field)
        old=Quiz.parse(legacy)
        self.assertEqual(len(old.missing_answer_details),3)

    def test_edit_resets_approved_to_draft_and_blocks_rendered(self):
        with tempfile.TemporaryDirectory() as td:
            store=Store(Path(td)/"db.sqlite3")
            ident=store.insert_question(question())
            store.approve([ident])
            result=store.update_answer_details(ident,{"wrong1_meaning":"Bia lager"})
            self.assertEqual(result["status"],"draft")
            self.assertEqual(store.get_questions([ident])[0].wrong1_meaning,"Bia lager")
            store.approve([ident])
            file=Path(td)/"test.mp4";file.write_bytes(b"fake")
            store.mark_rendered([ident],file)
            with self.assertRaises(ValueError):
                store.update_answer_details(ident,{"wrong1_meaning":"wrong"})

    def test_scheduled_video_cannot_be_modified(self):
        with tempfile.TemporaryDirectory() as td:
            store=Store(Path(td)/"data.sqlite3")
            video=Path(td)/"scheduled.mp4"
            video.write_bytes(b"fake")
            self.assertFalse(store.has_pending_page_job(video))
            store.add_job(video,"caption","2030-01-01T07:00:00+07:00")
            self.assertTrue(store.has_pending_page_job(video))

    def test_ai_enrichment_retains_manual_data(self):
        q=Quiz(topic="Đồ uống",question="Nước",correct="물",
                wrong1="맥주",wrong2="콜라",wrong3="차",meaning="Nước")
        expected={
            "romanization":"mul","meaning":"Water",
            "wrong1_romanization":"maekju","wrong1_meaning":"Bia",
            "wrong2_romanization":"kolla","wrong2_meaning":"Cola",
            "wrong3_romanization":"cha","wrong3_meaning":"Trà"
        }
        result={"candidates":[{"content":{"parts":[{"text":json.dumps(expected)}]}}]}
        with patch("studio.answer_enrichment.next_key",return_value="dummy"),\
             patch("studio.answer_enrichment.get_model",return_value="mock"),\
             patch("studio.answer_enrichment.post",return_value=result):
            updated,_=suggest(q)
        self.assertEqual(updated.answer_details("물")["meaning"],"Nước")
        self.assertEqual(updated.answer_details("차")["meaning"],"Trà")
        self.assertEqual(updated.missing_answer_details,[])

    def test_custom_logo_position(self):
        with tempfile.TemporaryDirectory() as td:
            logo=Path(td)/"logo.png"
            Image.new("RGBA",(40,20),(255,0,0,255)).save(logo)
            base=Image.new("RGB",(540,960),"white")
            result=_logo_overlay(base,logo,"custom",.15,logo_x=.34,logo_y=.52)
            self.assertEqual(result.getpixel((170,489)),(255,0,0))
            self.assertEqual(result.getpixel((5,5)),(255,255,255))
            settings=Production(td)
            new=settings.update({"logo_position":"custom","logo_x":.34,"logo_y":.52,
                                 "metadata_artist":"Tiếng Hàn So Easy"})
            self.assertEqual(new["logo_x"],.34)
            with self.assertRaises(ValueError):settings.update({"logo_x":2})

class FakeService:
    class Store:
        def get_questions(self,ids,status=None):return list(ids)
        def create_production_run(self,*args,**kwargs):pass
        def update_production_run(self,*args,**kwargs):pass
    def __init__(self):self.store=self.Store()
    def render_approved(self,ids,theme,with_voice,voice_engine,progress):
        progress(10,"Creating voice")
        progress(55,"Rendering frames")
        progress(98,"Writing metadata")
        return "completed-"+str(ids[0])+".mp4"

class QueueTests(unittest.TestCase):
    def test_completion_and_batch_progress(self):
        queue=ExportQueue(FakeService())
        created=queue.start(["1","2","3"],per_video=2)
        states=[]
        for _ in range(200):
            status=queue.status(created["job_id"])
            states.append(status["percent"])
            if status["status"]=="completed":break
            time.sleep(.01)
        self.assertEqual(status["status"],"completed")
        self.assertEqual(status["percent"],100)
        self.assertEqual(len(status["videos"]),2)
        self.assertEqual(states,sorted(states))
        self.assertEqual(status["total_videos"],2)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "Needs FFmpeg and FFprobe")
    def test_mp4_tags_are_present_after_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            src=Path(td)/"media.mp4"
            subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y",
                 "-f","lavfi","-i","color=c=black:s=320x568:r=24:d=1",
                 "-c:v","libx264","-pix_fmt","yuv420p",str(src)],
                 check=True,timeout=30,**hidden_subprocess())
            before=src.stat().st_size
            result=overwrite_metadata(src,{"metadata_title":"Quiz tiếng Hàn",
                "metadata_artist":"Tiếng Hàn So Easy",
                "metadata_copyright":"© Tiếng Hàn So Easy",
                "metadata_comment":"tienghansoeasy.io.vn"})
            self.assertEqual(result["tags"]["artist"],"Tiếng Hàn So Easy")
            info=subprocess.run(["ffprobe","-v","error","-show_entries",
                "format_tags=title,artist,copyright,comment","-of","json",str(src)],
                stdout=subprocess.PIPE,check=True,timeout=30,**hidden_subprocess())
            tags=json.loads(info.stdout)["format"]["tags"]
            self.assertEqual(tags["title"],"Quiz tiếng Hàn")
            self.assertEqual(tags["artist"],"Tiếng Hàn So Easy")
            self.assertEqual(tags["copyright"],"© Tiếng Hàn So Easy")
            self.assertGreater(src.stat().st_size,0)
            self.assertGreater(before,0)

if __name__=="__main__":
    unittest.main()
