"""Production settings, logo preview, SFX and batch migrations."""
import base64
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image
from studio.production import Production
from studio.database import Store
from studio.quiz import Quiz
from studio.sfx import build_sfx
from studio.renderer import draw_frame,KOREAN_PATHS,VIETNAMESE_REGULAR
from studio.service import Service

def quiz():
    return Quiz(topic="Giao tiếp",question="Từ nào có nghĩa là điện thoại dùng để liên lạc công việc?",
       correct="전화",wrong1="우유",wrong2="모자",wrong3="구두",level="TOPIK 1")

class ProductionTests(unittest.TestCase):
    def test_custom_folders_and_settings_persist(self):
        with tempfile.TemporaryDirectory() as td:
            prod=Production(td)
            current=prod.settings()
            self.assertGreaterEqual(current["think"],5)
            cfg=prod.update({"think":7,"reveal":3.5,"export_dir":str(Path(td)/"output"),
                             "cache_dir":str(Path(td)/"cache"),"logo_position":"bottom-left"})
            self.assertEqual(Production(td).settings()["think"],7)
            self.assertTrue(Path(cfg["export_dir"]).is_dir())
            with self.assertRaises(ValueError):prod.update({"think":.5})
            with self.assertRaises(ValueError):prod.update({"logo_position":"../bad"})
            with self.assertRaises(ValueError):prod.update({"unknown":True})

    def test_logo_upload_and_validation(self):
        with tempfile.TemporaryDirectory() as td:
            prod=Production(td)
            image=Image.new("RGBA",(110,80),(10,120,210,210))
            buf=io.BytesIO();image.save(buf,format="PNG")
            result=prod.set_logo(base64.b64encode(buf.getvalue()).decode())
            self.assertTrue(result["logo"])
            self.assertTrue(prod.logo().is_file())
            with self.assertRaises(ValueError):prod.set_logo("NOT_BASE64")

    def test_batch_state_transition(self):
        with tempfile.TemporaryDirectory() as td:
            store=Store(Path(td)/"db.sqlite")
            one=store.insert_question(quiz(),batch_id="batch-2026")
            self.assertEqual(store.list_batches()[0]["drafts"],1)
            store.approve([one])
            path=Path(td)/"out.mp4";path.write_bytes(b"FAKE VIDEO")
            store.mark_rendered([one],path)
            self.assertEqual(store.list_questions()[0]["status"],"rendered")
            self.assertEqual(store.list_batches()[0]["rendered"],1)
            self.assertEqual(store.get_video(path)["status"],"rendered")
            with self.assertRaises(ValueError):store.approve([one])
            with self.assertRaises(ValueError):store.mark_rendered([one],path)

    def test_sfx_track_contains_sound(self):
        import wave
        with tempfile.TemporaryDirectory() as td:
            file=build_sfx(Path(td)/"sound.wav",2,5.5,2.8,.8,1.5,.8)
            with wave.open(str(file),"rb") as wf:
                data=wf.readframes(wf.getnframes())
                self.assertEqual(wf.getframerate(),44100)
                self.assertGreater(len(data),100000)
                self.assertNotEqual(set(data),{0})

    @unittest.skipUnless(any(Path(p).exists() for p in KOREAN_PATHS) and
                         any(Path(p).exists() for p in VIETNAMESE_REGULAR),
                         "Requires Unicode Korean and Vietnamese installed fonts")
    def test_preview_with_logo_keeps_text_rendering(self):
        with tempfile.TemporaryDirectory() as td:
            service=Service(td)
            qid=service.store.insert_question(quiz())
            img=Image.new("RGBA",(80,60),(255,0,0,255))
            buffer=io.BytesIO();img.save(buffer,format="PNG")
            service.production.set_logo(base64.b64encode(buffer.getvalue()).decode())
            preview=service.preview(qid,"hanji")
            self.assertGreater(len(preview),10000)
            with Image.open(io.BytesIO(preview)) as result:
                self.assertEqual(result.size,(540,960))
                self.assertEqual(result.mode,"RGB")

if __name__=="__main__":unittest.main()
