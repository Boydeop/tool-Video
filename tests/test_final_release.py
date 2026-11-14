"""Final-release regression tests: Autopilot, persistence, Shorts, cache and platforms.

All external providers are mocked. Never use live credentials in CI.
"""
from datetime import datetime,timezone,timedelta
from pathlib import Path
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from PIL import Image
from studio.autopilot import Plan,choose_distinct_topics,run_pipeline
from studio.naming import video_name,slug
from studio.cache_manager import CacheManager
from studio.quality import inspect_quiz
from studio.database import Store
from studio.quiz import Quiz
from studio.production import Production
from studio.service import Service
from studio.youtube import YouTubeAccount
from studio.instagram import InstagramAccount,validate_public_url
from studio.renderer import shorts_safe_frame,THEMES
from studio.scheduler import Scheduler

def quiz(number=0,complete=True):
    choices=["물","차","학교","사람","강아지","사과","책","바다","지하철",
             "병원","음악","어머니","아버지","책상","연필","컴퓨터",
             "창문","아침","저녁","친구","선생님","학생","고양이",
             "물고기","전화","시간","회사","도시","공원","시장"]
    word=choices[number%len(choices)]
    others=[w for w in choices if w!=word][:3]
    fields={
        "topic":"Gia đình","question":"Từ vựng "+str(number),
        "clue":"Từ vựng "+str(number),"meaning":"Từ vựng "+str(number),
        "correct":word,"wrong1":others[0],"wrong2":others[1],"wrong3":others[2],
        "romanization":"eoneo","wrong1_romanization":"hana",
        "wrong2_romanization":"dul","wrong3_romanization":"set",
        "wrong1_meaning":"Nghĩa 1","wrong2_meaning":"Nghĩa 2",
        "wrong3_meaning":"Nghĩa 3","level":"TOPIK 1"}
    if not complete:fields["wrong2_meaning"]=""
    return Quiz(**fields)

class FinalNamingTests(unittest.TestCase):
    def test_stable_sortable_filename_and_slug(self):
        self.assertEqual(slug("Thời gian và ngày tháng"),"thoi-gian-va-ngay-thang")
        dt=datetime(2026,11,13,tzinfo=timezone(timedelta(hours=7)))
        self.assertEqual(video_name(17,"Thời gian và ngày tháng","TOPIK 2",1080,dt),
            "THSE_20261113_000017_thoi-gian-va-ngay-thang_TOPIK2_1080x1920.mp4")
        with self.assertRaises(ValueError):video_name(0,"bad")

    def test_atomic_unique_counter_and_history(self):
        with tempfile.TemporaryDirectory() as td:
            store=Store(Path(td)/"data.sqlite3")
            numbers=[]
            lock=threading.Lock()
            def worker():
                for _ in range(8):
                    n=store.allocate_video_number()
                    with lock:numbers.append(n)
            workers=[threading.Thread(target=worker) for _ in range(4)]
            for t in workers:t.start()
            for t in workers:t.join()
            self.assertEqual(sorted(numbers),list(range(1,33)))
            self.assertEqual(Store(Path(td)/"data.sqlite3").allocate_video_number(),33)
            store.create_production_run("run-1",{"mode":"autopilot"})
            store.update_production_run("run-1","running",[{"topic":"Gia đình"}])
            store.recover_incomplete_runs()
            self.assertEqual(store.list_production_runs()[0]["status"],"interrupted")

class CacheTests(unittest.TestCase):
    def test_whitelist_cleanup_preserves_exports_and_user_files(self):
        with tempfile.TemporaryDirectory() as td:
            base=Path(td)
            cache=base/"cache";cache.mkdir()
            (cache/("a"*64+".mp3")).write_bytes(b"tts")
            (cache/("b"*64+".working.mp3")).write_bytes(b"working")
            (cache/"my_voice.mp3").write_bytes(b"USER VOICE")
            preview=cache/"video_previews";preview.mkdir()
            (preview/"preview_abc123abc123.mp4").write_bytes(b"preview")
            (preview/"my-original.mp4").write_bytes(b"USER VIDEO")
            output=base/"THSE_0001.mp4";output.write_bytes(b"PRODUCTION")
            manager=CacheManager(cache)
            self.assertEqual(manager.stats()["files"],3)
            dry=manager.clear(dry_run=True)
            self.assertEqual(dry["files"],3)
            self.assertTrue((cache/("a"*64+".mp3")).exists())
            cleaned=manager.clear(dry_run=False)
            self.assertEqual(cleaned["files"],3)
            self.assertTrue((cache/"my_voice.mp3").exists())
            self.assertTrue((preview/"my-original.mp4").exists())
            self.assertEqual(output.read_bytes(),b"PRODUCTION")

class QualityAndAutopilotTests(unittest.TestCase):
    def test_syntactic_gate_requires_all_four_meanings(self):
        self.assertEqual(inspect_quiz(quiz(0)),[])
        self.assertTrue(inspect_quiz(quiz(1,False)))
        self.assertEqual(Plan.parse({"videos":8}).videos,8)
        with self.assertRaises(ValueError):Plan.parse({"videos":101})

    def test_choose_unique_topics_across_run(self):
        selected=choose_distinct_topics({"Gia đình":500},10)
        self.assertEqual(len(selected),10)
        self.assertEqual(len(set(selected)),10)

    def test_bounded_pipeline_auto_renders_without_checkboxes(self):
        with tempfile.TemporaryDirectory() as td:
            service=Service(td)
            state={"index":0}
            def generate(topic,level,count,fmt):
                state["index"]+=1
                q=quiz(state["index"])
                from dataclasses import replace
                q=replace(q,topic=topic,level=level)
                ident=service.store.insert_question(q,batch_id="run")
                return {"accepted":[ident]}
            def render(ids,theme,width,with_voice,voice_engine,progress):
                self.assertTrue(all(service.store.get_questions(ids)[0].meaning for _ in ids))
                target=Path(td)/("render_"+str(state["index"])+".mp4")
                target.write_bytes(b"placeholder")
                service.store.mark_rendered(ids,target)
                progress(100,"rendered")
                return str(target)
            service.generate=generate
            service.render_approved=render
            def verify(qid,mode="strict"):
                service.store.set_question_qa(qid,"verified",
                    {"outcome":"verified","tier":"test_mock"})
                return {"status":"verified","tier":"test_mock","issues":[]}
            service.verify_generated=verify
            changes=[]
            result=run_pipeline(service,Plan.parse({"videos":2,"per_video":1}),
                           lambda x,y:changes.append((x,y)))
            self.assertEqual(result["rendered"],2)
            self.assertEqual(len({i["topic"] for i in result["topics"]}),2)
            self.assertEqual(len(service.store.list_videos()),2)
            self.assertTrue(changes)

    def test_seo_failure_keeps_video_done(self):
        with tempfile.TemporaryDirectory() as td:
            service=Service(td)
            from dataclasses import replace
            def generate(topic,level,count,fmt):
                q=replace(quiz(17),topic=topic,level=level)
                return {"accepted":[service.store.insert_question(q)]}
            def render(ids,theme,width,with_voice,voice_engine,progress):
                path=Path(td)/"ready.mp4";path.write_bytes(b"done")
                service.store.mark_rendered(ids,path)
                return str(path)
            service.generate=generate
            service.render_approved=render
            def verify(qid,mode="strict"):
                service.store.set_question_qa(qid,"verified",
                    {"outcome":"verified","tier":"test_mock"})
                return {"status":"verified","tier":"test_mock","issues":[]}
            service.verify_generated=verify
            service.auto_seo_for_video=lambda path:(_ for _ in ()).throw(RuntimeError("No API"))
            result=run_pipeline(service,Plan.parse({"videos":1,"per_video":1,"auto_seo":True}),
                                lambda a,b:None)
            self.assertEqual(result["rendered"],1)
            self.assertEqual(result["topics"][0]["status"],"rendered")
            self.assertEqual(result["topics"][0]["seo_source"],"unavailable")

    def test_missing_details_are_not_auto_published(self):
        with tempfile.TemporaryDirectory() as td:
            service=Service(td)
            def generate(topic,level,count,fmt):
                ident=service.store.insert_question(quiz(2,False))
                return {"accepted":[ident]}
            service.generate=generate
            result=run_pipeline(service,Plan.parse({"videos":1,"per_video":1}),
                                lambda x,y:None)
            self.assertEqual(result["rendered"],0)
            self.assertEqual(result["topics"][0]["status"],"needs_review")
            self.assertEqual(service.store.list_questions()[0]["status"],"draft")

class ShortProfileTests(unittest.TestCase):
    def test_shorts_safe_area_and_default_settings(self):
        source=Image.new("RGB",(540,960),"white")
        image=shorts_safe_frame(source,"hanji")
        self.assertEqual(image.size,(540,960))
        self.assertEqual(image.getpixel((25,70)),(255,255,255))
        self.assertEqual(image.getpixel((530,930)),Image.new("RGB",(1,1),THEMES["hanji"][0]).getpixel((0,0)))
        with tempfile.TemporaryDirectory() as td:
            cfg=Production(td).settings()
            self.assertEqual(cfg["width"],1080)
            self.assertEqual(cfg["fps"],30)
            self.assertTrue(cfg["shorts_safe"])

    def test_render_name_from_sql_sequence_and_metadata(self):
        with tempfile.TemporaryDirectory() as td:
            service=Service(td)
            ident=service.store.insert_question(quiz(0))
            service.store.approve([ident])
            def render(_items,path,**kwargs):
                Path(path).parent.mkdir(parents=True,exist_ok=True)
                Path(path).write_bytes(b"test")
                self.assertTrue(kwargs["shorts_safe"])
                return str(path)
            with patch("studio.service.render",side_effect=render),\
                 patch("studio.metadata.overwrite_metadata",return_value={}) as tags:
                path=service.render_approved([ident],width=1080)
            self.assertIn("Gia đình",tags.call_args.args[1]["metadata_title"])
            self.assertRegex(Path(path).name,r"^THSE_\d{8}_000001_gia-dinh_TOPIK1_1080x1920\.mp4$")
            self.assertEqual(service.store.list_questions()[0]["status"],"rendered")

class CrossPlatformTests(unittest.TestCase):
    def test_instagram_rejects_private_url(self):
        for value in ("http://example.com/a.mp4","https://127.0.0.1/a.mp4",
                      "https://10.1.2.3/video.mp4","https://localhost/a.mp4"):
            with self.assertRaises(ValueError):validate_public_url(value)
        self.assertEqual(validate_public_url("https://cdn.example.com/video.mp4"),
                         "https://cdn.example.com/video.mp4")

    def test_instagram_connect_and_post_mock(self):
        with tempfile.TemporaryDirectory() as td:
            account=InstagramAccount(td)
            with patch("studio.instagram._graph",return_value={"id":"1234567","username":"testaccount"}):
                result=account.connect("1234567","LOCAL_SAMPLE_ACCESS_TOKEN_123456")
            self.assertTrue(result["connected"])
            def fake_graph(path,token,values=None,method="GET"):
                if path.endswith("/media"):return {"id":"999888777"}
                if path=="999888777":return {"status_code":"FINISHED"}
                if path.endswith("/media_publish"):return {"id":"222333444"}
                raise AssertionError(path)
            with patch("studio.instagram._graph",side_effect=fake_graph),\
                 patch("studio.instagram.time.sleep",return_value=None):
                ident=account.publish("https://cdn.example.com/video.mp4","A video")
            self.assertEqual(ident,"222333444")
            self.assertFalse(account.disconnect()["connected"])

    def test_youtube_desktop_oauth_client_config_never_returns_secret(self):
        with tempfile.TemporaryDirectory() as td:
            account=YouTubeAccount(td)
            client={"installed":{
                "client_id":"1234567890-abcdef.apps.googleusercontent.com",
                "client_secret":"GOCSPX_sample_client_secret",
                "auth_uri":"https://accounts.google.com/o/oauth2/auth",
                "token_uri":"https://oauth2.googleapis.com/token",
                "redirect_uris":["http://localhost"]}}
            result=account.configure(client)
            self.assertTrue(result["client_configured"])
            self.assertNotIn("client_secret",json.dumps(result))
            with self.assertRaises(ValueError):account.configure({"web":{}})
            self.assertFalse(account.status()["connected"])

    def test_outbound_independent_platform_jobs_and_recovery(self):
        with tempfile.TemporaryDirectory() as td:
            store=Store(Path(td)/"data.sqlite3")
            file=Path(td)/"test.mp4";file.write_bytes(b"test")
            future=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat()
            yid=store.schedule_outbound(file,"youtube","Title","Caption",future)
            iid=store.schedule_outbound(file,"instagram","Title","Caption",future,
                                         "https://cdn.example.com/video.mp4")
            with self.assertRaises(Exception):
                store.schedule_outbound(file,"youtube","Title","Caption",future)
            with store.db() as c:
                c.execute("UPDATE outbound_posts SET publish_at=?",(datetime(2020,1,1,tzinfo=timezone.utc).isoformat(),))
            scheduler=Scheduler(store)
            with patch.object(scheduler.youtube,"upload",return_value="abcdEF012345"),\
                 patch.object(scheduler.instagram,"publish",return_value="987654"):
                scheduler.tick()
            statuses={p["platform"]:p["status"] for p in store.list_outbound()}
            self.assertEqual(statuses,{"youtube":"submitted","instagram":"submitted"})
            with store.db() as c:
                c.execute("UPDATE outbound_posts SET status='publishing' WHERE id=?",(yid,))
            store.recover_incomplete_runs()
            job=next(x for x in store.list_outbound() if x["id"]==yid)
            self.assertEqual(job["status"],"needs_review")

if __name__=="__main__":unittest.main()
