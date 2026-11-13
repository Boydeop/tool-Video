"""v1.1 regression: date-aware events, resilience, metadata and balanced Shorts."""
from __future__ import annotations
from dataclasses import replace
from datetime import date
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from studio.events import upcoming_events,resolve_event,event_topics
from studio.curated_content import fill,BANKS,EVENT_BANKS
from studio.autopilot import Plan,run_pipeline
from studio.database import Store
from studio.quality import inspect_quiz
from studio.production import Production
from studio.service import Service
from studio.renderer import shorts_safe_frame, korean_background,draw_frame,KOREAN_PATHS,VIETNAMESE_REGULAR
from studio.quiz import Quiz

class CalendarTests(unittest.TestCase):
    def test_christmas_detected_from_mid_november(self):
        events=upcoming_events(date(2026,11,13),window_days=55)
        christmas=next(e for e in events if e["id"]=="christmas")
        self.assertEqual(christmas["date"],"2026-12-25")
        self.assertEqual(christmas["days_until"],42)
        self.assertEqual(resolve_event("christmas",date(2026,11,13))["id"],"christmas")
        self.assertEqual(len(set(event_topics("christmas",12))),12)

    def test_events_are_separate_from_random_and_future_only(self):
        self.assertEqual(upcoming_events(date(2026,7,1),window_days=35),[])
        with self.assertRaises(ValueError):
            resolve_event("christmas",date(2026,7,1))
        self.assertEqual(event_topics("hangul_day",2)[0].startswith("한글날"),True)
        with self.assertRaises(ValueError):
            event_topics("hangul_day",12)
        with patch("studio.events.today_vietnam",return_value=date(2026,11,13)):
            plan=Plan.parse({"topic_mode":"seasonal","event_id":"christmas","videos":4})
            self.assertEqual(plan.topic_mode,"seasonal")
            self.assertEqual(plan.event_id,"christmas")

class CuratedTests(unittest.TestCase):
    def test_curated_examples_are_all_complete_and_topic_specific(self):
        for topic,options in BANKS.items():
            self.assertGreaterEqual(len(options),8)
            with tempfile.TemporaryDirectory() as td:
                store=Store(Path(td)/"db.sqlite3")
                ids=fill(store,topic,"TOPIK 1",5)
                self.assertGreaterEqual(len(ids),4)
                for q in store.get_questions(ids):
                    self.assertEqual(inspect_quiz(q),[])
                    self.assertEqual(q.topic,topic)
        self.assertGreaterEqual(len(EVENT_BANKS["christmas"]),20)

    def test_duplicate_curated_inputs_are_not_reinserted(self):
        with tempfile.TemporaryDirectory() as td:
            store=Store(Path(td)/"db.sqlite3")
            ids=fill(store,"Màu sắc","TOPIK 1",6)
            newer=fill(store,"Màu sắc","TOPIK 1",6)
            self.assertTrue(ids)
            self.assertFalse(set(ids)&set(newer))
            self.assertEqual(len(store.get_questions(ids)),len(ids))

class ProductionPipelineTests(unittest.TestCase):
    @staticmethod
    def attach_fake_renderer(service,root):
        calls=[]
        def render(ids,theme,width,with_voice,voice_engine,progress):
            qs=service.store.get_questions(ids,status=("approved","auto_approved"))
            assert len({q.topic for q in qs})==1
            assert all(inspect_quiz(q)==[] for q in qs)
            path=Path(root)/("film_"+str(len(calls))+".mp4")
            path.write_bytes(b"MP4 MOCK")
            service.store.mark_rendered(ids,path)
            calls.append({"topic":qs[0].topic,"questions":len(ids),"path":str(path)})
            progress(100,"complete")
            return str(path)
        service.render_approved=render
        return calls

    def test_no_api_random_mode_still_produces_four_distinct_videos(self):
        with tempfile.TemporaryDirectory() as td:
            service=Service(td)
            calls=self.attach_fake_renderer(service,td)
            service.generate=lambda topic,level,count,fmt: {
                "accepted":[],"errors":["All providers unavailable"]}
            plan=Plan.parse({"videos":4,"per_video":5,
                             "auto_seo":False,"auto_enrich":False})
            output=run_pipeline(service,plan,lambda x,y:None)
            self.assertEqual(output["rendered"],4)
            self.assertEqual(len(set(v["topic"] for v in calls)),4)
            self.assertEqual(len(service.store.list_videos()),4)
            self.assertTrue(all(x["source"]=="curated_fallback" for x in output["topics"]))
            self.assertTrue(all(v["questions"]>=2 for v in calls))

    def test_no_api_christmas_generates_distinct_seasonal_subtopics(self):
        with tempfile.TemporaryDirectory() as td:
            service=Service(td)
            calls=self.attach_fake_renderer(service,td)
            service.generate=lambda topic,level,count,fmt: {"accepted":[]}
            with patch("studio.events.today_vietnam",return_value=date(2026,11,13)):
                plan=Plan.parse({"videos":4,"per_video":5,"topic_mode":"seasonal",
                                 "event_id":"christmas","auto_enrich":False})
                output=run_pipeline(service,plan,lambda x,y:None)
            self.assertEqual(output["rendered"],4)
            self.assertEqual(len(set(v["topic"] for v in calls)),4)
            self.assertTrue(all("Noel" in row["topic"] or "Giáng sinh" in row["topic"] or
                                "Mùa đông" in row["topic"] or "Đêm Giáng sinh" in row["topic"]
                                for row in output["topics"]))
            self.assertTrue(all(row["event"]["id"]=="christmas" for row in output["topics"]))

    def test_partial_valid_batch_renders_short_not_zero_videos(self):
        with tempfile.TemporaryDirectory() as td:
            service=Service(td)
            self.attach_fake_renderer(service,td)
            options=BANKS["Gia đình"]
            ids=fill(service.store,"Gia đình","TOPIK 1",3)
            used=False
            def generate(topic,level,count,fmt):
                nonlocal used
                if not used:
                    used=True
                    return {"accepted":ids}
                return {"accepted":[],"errors":["quota reached"]}
            service.generate=generate
            service.store.topic_counts=lambda: {"Gia đình":0} # chosen via patch
            with patch("studio.autopilot.choose_distinct_topics",return_value=["Gia đình"]):
                out=run_pipeline(service,Plan.parse({"videos":1,"per_video":5,
                                        "curated_fallback":False}),lambda x,y:None)
            self.assertEqual(out["rendered"],1)
            self.assertEqual(out["topics"][0]["status"],"rendered_short")
            self.assertEqual(out["topics"][0]["accepted"],3)

    def test_missing_semantics_never_render_without_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            service=Service(td)
            options=BANKS["Màu sắc"]
            from studio.curated_content import construct
            bad=replace(construct(options[0],"Màu sắc","TOPIK 1",options,0),
                        wrong3_meaning="")
            ids=[service.store.insert_question(bad)]
            service.generate=lambda topic,level,count,fmt: {"accepted":ids if count else []}
            with patch("studio.autopilot.choose_distinct_topics",return_value=["Màu sắc"]):
                result=run_pipeline(service,Plan.parse({"videos":1,"per_video":5,
                    "curated_fallback":False,"auto_enrich":False}),lambda x,y:None)
            self.assertEqual(result["rendered"],0)
            self.assertEqual(result["topics"][0]["status"],"needs_review")

class BrandAndShortsTests(unittest.TestCase):
    def test_brand_metadata_is_automatic_but_manual_override_remains(self):
        with tempfile.TemporaryDirectory() as td:
            prod=Production(td)
            self.assertTrue(prod.settings()["metadata_auto"])
            auto=prod.video_metadata("Màu sắc","TOPIK 1",seo_title="Học màu sắc")
            self.assertEqual(auto["metadata_artist"],"Tiếng Hàn So Easy")
            self.assertEqual(auto["metadata_title"],"Học màu sắc")
            self.assertIn("tienghansoeasy.io.vn",auto["metadata_copyright"])
            prod.update({"metadata_auto":False,"metadata_artist":"Tác giả thử",
                         "metadata_copyright":"© Tác giả thử"})
            other=prod.video_metadata("Bất kỳ","TOPIK 3")
            self.assertEqual(other["metadata_artist"],"Tác giả thử")
            self.assertEqual(other["metadata_copyright"],"© Tác giả thử")

    def test_safe_frame_is_balanced_and_has_full_canvas_motifs(self):
        original=Image.new("RGB",(540,960),"white")
        safe=shorts_safe_frame(original,"sejong")
        self.assertEqual(safe.size,(540,960))
        self.assertEqual(safe.getpixel((40,70)),(255,255,255))
        self.assertEqual(safe.getpixel((475,830)),(255,255,255))
        self.assertNotEqual(safe.getpixel((520,935)),(255,255,255))
        for theme in ("hanji","blue","sejong"):
            bg=korean_background(theme)
            self.assertEqual(bg.size,(540,960))
            self.assertNotEqual(bg.getpixel((66,173)),bg.getpixel((350,400)))

    @unittest.skipUnless(any(Path(p).is_file() for p in KOREAN_PATHS) and
                         any(Path(p).is_file() for p in VIETNAMESE_REGULAR),
                         "Host does not have Korean/Vietnamese fonts")
    def test_legacy_preview_still_valid(self):
        q=Quiz(topic="Màu sắc",question="Màu đỏ",clue="Màu đỏ",
             correct="빨강",romanization="ppalgang",meaning="Màu đỏ",
             wrong1="파랑",wrong1_romanization="parang",wrong1_meaning="Màu xanh",
             wrong2="노랑",wrong2_romanization="norang",wrong2_meaning="Màu vàng",
             wrong3="하양",wrong3_romanization="hayang",wrong3_meaning="Màu trắng")
        image=draw_frame(q,1,1,"sejong",elapsed=2,shorts_safe=True)
        self.assertEqual(image.size,(540,960))

class FrontEndTests(unittest.TestCase):
    def test_ui_5_tabs_separate_event_and_random(self):
        html=(Path(__file__).resolve().parents[1]/"studio"/"ui.html").read_text(encoding="utf-8")
        self.assertEqual(html.count('data-nav="autopilot"'),1)
        self.assertNotIn('data-nav="create"',html)
        for control in ("eventPlanner","eventSelect","metadataAuto",
                        "manualMetadata","autoEnrich","autoFallback",
                        "autoStart","manualGeneration","manualRenderSettings"):
            self.assertEqual(html.count('id="'+control+'"'),1,control)
        self.assertIn('topic_mode:',html)
        self.assertIn('function loadEvents',html)
        self.assertIn('shorts_safe:',html)

if __name__=="__main__":unittest.main()
