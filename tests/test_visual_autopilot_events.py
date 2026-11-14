"""v1.1 visual/automation regressions. No external API or real secrets."""
import json
from datetime import date
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
import tempfile
import unittest

from studio.events import upcoming_events,choose_production_topics
from studio.autopilot import Plan,run_pipeline
from studio.quality import inspect_quiz
from studio.renderer import _korea_background,shorts_safe_frame,THEMES
from studio.production import Production
from studio.service import Service
from studio.quiz import Quiz

def sample(clue="Khúc xương",meaning="Khúc xương",complete=True):
    return Quiz(topic="Ẩm thực Hàn Quốc",question=clue,clue=clue,
        correct="뼈",wrong1="물",wrong2="우유",wrong3="고기",
        meaning=meaning,romanization="ppyeo",
        wrong1_meaning="Nước",wrong1_romanization="mul",
        wrong2_meaning="Sữa",wrong2_romanization="uyu",
        wrong3_meaning="Thịt" if complete else "",
        wrong3_romanization="gogi",level="TOPIK 1")

class SeasonalTests(unittest.TestCase):
    def test_christmas_2026_close(self):
        events=upcoming_events(date(2026,11,13))
        christmas=next(e for e in events if e["key"]=="christmas")
        self.assertEqual(christmas["date"],"2026-12-25")
        self.assertEqual(christmas["days_until"],42)
        self.assertIn("Giáng sinh",christmas["name"])
    def test_random_and_events_are_separate(self):
        random_topics,unused=choose_production_topics({},4,"random",date(2026,11,13))
        self.assertFalse(unused)
        self.assertFalse(any("Giáng sinh" in topic for topic in random_topics))
        mixed,events=choose_production_topics({},4,"mix",date(2026,11,13))
        self.assertEqual(len(set(mixed)),4)
        self.assertTrue(any("Giáng sinh" in topic or "Noel" in topic for topic in mixed))
        self.assertTrue(events)
        just_event,_=choose_production_topics({},4,"events",date(2026,11,13))
        self.assertEqual(len(set(just_event)),4)
    def test_no_lunar_date_guesses_or_stale_events(self):
        self.assertTrue(all(e["days_until"]>=0 for e in upcoming_events(date(2026,11,13))))
        self.assertFalse(any("Chuseok" in e["name"] for e in upcoming_events(date(2026,11,13))))

class RenderingTests(unittest.TestCase):
    def test_art_has_korean_cultural_motifs_and_balanced_safe_margin(self):
        for theme in THEMES:
            image=_korea_background(theme)
            self.assertEqual(image.size,(540,960))
            bg=THEMES[theme][0]
            from PIL import Image
            plain=Image.new("RGB",(540,960),bg)
            # A visible vector layer must exist, not a plain solid fill.
            self.assertNotEqual(image.tobytes(),plain.tobytes())
            safe=shorts_safe_frame(Image.new("RGB",(540,960),"white"),theme)
            self.assertEqual(safe.size,(540,960))
            # No v1.0.0 tiny centered 421px content: art spans x=24..515.
            self.assertEqual(safe.getpixel((500,120)),(255,255,255))
            self.assertNotEqual(safe.getpixel((525,930)),(255,255,255))
    def test_brand_metadata_defaults_and_empty_config_migration(self):
        with tempfile.TemporaryDirectory() as td:
            p=Production(td)
            p.file.write_text(json.dumps({"metadata_artist":"",
                 "metadata_copyright":"","metadata_comment":""}),encoding="utf-8")
            cfg=p.settings()
            self.assertTrue(cfg["auto_metadata"])
            self.assertEqual(cfg["metadata_artist"],"Tiếng Hàn So Easy")
            self.assertIn("Tiếng Hàn",cfg["metadata_copyright"])

class AutopilotRepairTests(unittest.TestCase):
    def test_contextual_clue_is_not_dropped_as_translation_mismatch(self):
        contextual=sample("Giá rẻ nhưng","nhưng")
        self.assertEqual(inspect_quiz(contextual),[])
    def test_auto_repairs_missing_answer_details_without_checkbox(self):
        with tempfile.TemporaryDirectory() as td:
            service=Service(td)
            qid=service.store.insert_question(sample(complete=False))
            from studio.answer_enrichment import DETAIL_KEYS
            def generate(*args,**kwargs):
                return {"accepted":[qid],"errors":[]}
            def enrich(ids):
                service.store.update_answer_details(ids[0],
                   {"wrong3_meaning":"Thịt"})
            def render(ids,theme,width,with_voice,voice_engine,progress):
                self.assertEqual(ids,[qid])
                output=Path(td)/"rendered.mp4";output.write_bytes(b"mock")
                service.store.mark_rendered(ids,output)
                return str(output)
            service.generate=generate
            service.enrich_answer_details=enrich
            service.render_approved=render
            def verify(qid,mode="strict"):
                service.store.set_question_qa(qid,"verified",
                    {"outcome":"verified","tier":"test_mock"})
                return {"status":"verified","tier":"test_mock","issues":[]}
            service.verify_generated=verify
            plan=Plan.parse({"videos":1,"per_video":1})
            results=run_pipeline(service,plan,lambda a,b:None)
            self.assertEqual(results["rendered"],1)
            self.assertEqual(results["topics"][0]["status"],"rendered")
            self.assertFalse(service.store.get_questions([qid])[0].missing_answer_details)
    def test_failed_repair_keeps_draft_and_explains_why(self):
        with tempfile.TemporaryDirectory() as td:
            service=Service(td)
            qid=service.store.insert_question(sample(complete=False))
            service.generate=lambda *args,**kwargs:{"accepted":[qid],"errors":[]}
            service.enrich_answer_details=lambda ids: (_ for _ in ()).throw(RuntimeError("Quota"))
            result=run_pipeline(service,Plan.parse({"videos":1,"per_video":1}),
                                lambda a,b:None)
            self.assertEqual(result["rendered"],0)
            self.assertEqual(result["topics"][0]["status"],"needs_review")
            self.assertIn("Thiếu nghĩa",result["topics"][0]["reason"])
            self.assertEqual(service.store.list_questions()[0]["status"],"draft")

class InterfaceTests(unittest.TestCase):
    def test_control_integrity_and_no_forced_24fps(self):
        ui=(Path(__file__).resolve().parents[1]/"studio"/"ui.html").read_text("utf-8")
        for marker in ('id="autoEventMode"','id="eventCountdown"',
                       'id="autoMetadata"','id="metadataAdvanced"',
                       'id="manualExportDetails"',"renderUpcomingEvents()",
                       'fps:30','Tự gắn tác giả'):
            self.assertIn(marker,ui)
        self.assertNotIn('fps:24,',ui)

if __name__=="__main__":unittest.main()
