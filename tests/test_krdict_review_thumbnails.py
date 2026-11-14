"""v1.2 KRDict QA and thumbnails: all network calls mocked, no secrets."""
from dataclasses import replace
from datetime import datetime,timezone,timedelta
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from studio.krdict import parse_search,sense_overlap,KRDict,KRDictError
from studio.review import verify_quiz,blind_review
from studio.quiz import Quiz
from studio.database import Store
from studio.service import Service
from studio.scheduler import Scheduler
from studio.thumbnails import thumbnail_path,ensure_thumbnail
from studio.production import Production

XML=b"""<?xml version="1.0" encoding="UTF-8"?>
<channel><total>1</total><item><target_code>38122</target_code>
<word>\xeb\xac\xbc</word><pos>\xeb\xaa\x85\xec\x82\xac</pos>
<sense><translation><trans_lang>\xeb\xb2\xa0\xed\x8a\xb8\xeb\x82\xa8\xec\x96\xb4</trans_lang>
<trans_word>n\xc6\xb0\xe1\xbb\x9bc</trans_word><trans_dfn>Ch\xe1\xba\xa5t l\xe1\xbb\x8fng d\xc3\xb9ng \xc4\x91\xe1\xbb\x83 u\xe1\xbb\x91ng</trans_dfn>
</translation></sense></item>
<item><word>\xeb\xac\xbc\xea\xb3\xa0\xea\xb8\xb0</word></item></channel>"""

def quiz():
    return Quiz(topic="Đồ uống",question="Nước",clue="Nước",
        correct="물",wrong1="맥주",wrong2="차",wrong3="우유",
        meaning="Nước",romanization="mul",
        wrong1_meaning="Bia",wrong1_romanization="maekju",
        wrong2_meaning="Trà",wrong2_romanization="cha",
        wrong3_meaning="Sữa",wrong3_romanization="uyu")

class FakeDictionary:
    def __init__(self,missing=False,ambiguous=False):
        self.missing=missing;self.ambiguous=ambiguous
    def lookup(self,word):
        if word=="물" and self.missing:return {"exact":False,"entries":[]}
        definitions={"물":["Nước"],"맥주":["Bia"],"차":["Trà"],"우유":["Sữa"]}
        vals=definitions[word]
        if self.ambiguous and word=="맥주":vals=["Bia","Nước"]
        return {"exact":True,"entries":[{"word":word,"meanings":vals,
                                          "target_code":"123","pos":"명사"}]}

def reviewer(q,provider):
    return {"available":True,"provider":"groq","different_provider":True,
            "selected":q.correct,"back_translation":q.short_clue,
            "ambiguous":False,"conflicting_choices":[],"reason":"OK"}

class KRDictTests(unittest.TestCase):
    def test_official_xml_vietnamese_senses_only_exact_headword(self):
        value=parse_search(XML,"물")
        self.assertTrue(value["exact"])
        self.assertEqual(value["entries"][0]["word"],"물")
        self.assertIn("nước",value["entries"][0]["meanings"])
        self.assertFalse(parse_search(XML,"불")["exact"])
        self.assertGreaterEqual(sense_overlap("Nước",["nước"]),1)
        with self.assertRaises(KRDictError):parse_search(b"not xml","물")
    def test_transport_cache_and_no_key_in_history(self):
        with tempfile.TemporaryDirectory() as td:
            store=Store(Path(td)/"studio.sqlite3")
            queries=[]
            def transport(request):
                queries.append(request.full_url)
                return XML
            with patch("studio.krdict.get_keys",return_value=["a"*32]):
                data=KRDict(store,transport=transport).lookup("물")
                self.assertTrue(data["exact"])
                again=KRDict(store,transport=transport).lookup("물")
            self.assertTrue(again["cache_hit"])
            self.assertEqual(len(queries),1)
            self.assertIn("trans_lang=7",queries[0])
            self.assertIn("translated=y",queries[0])
            self.assertEqual(store.krdict_cache_get("물")["entries"][0]["word"],"물")
            with store.db() as c:
                jsondata=str(c.execute("SELECT payload_json FROM krdict_cache").fetchone()[0])
            self.assertNotIn("a"*32,jsondata)

class QAReviewTests(unittest.TestCase):
    def test_strict_requires_dictionary_and_blind_reverse(self):
        with tempfile.TemporaryDirectory() as td:
            store=Store(Path(td)/"test.sqlite3")
            verdict=verify_quiz(quiz(),store,dictionary=FakeDictionary(),reviewer=reviewer)
            self.assertEqual(verdict["outcome"],"verified")
            self.assertEqual(verdict["tier"],"dictionary+blind_ai")
            bad=verify_quiz(quiz(),store,dictionary=FakeDictionary(missing=True),
                            reviewer=reviewer)
            self.assertEqual(bad["outcome"],"needs_review")
            assisted=verify_quiz(quiz(),store,mode="assisted",
                                dictionary=FakeDictionary(missing=True),reviewer=reviewer)
            self.assertEqual(assisted["tier"],"blind_ai_only")
            self.assertEqual(assisted["outcome"],"verified")
            ambiguous=verify_quiz(quiz(),store,dictionary=FakeDictionary(ambiguous=True),
                                  reviewer=reviewer)
            self.assertEqual(ambiguous["outcome"],"needs_review")
    def test_reverse_judge_mismatch_blocks_even_if_dictionary_matches(self):
        with tempfile.TemporaryDirectory() as td:
            store=Store(Path(td)/"test.sqlite3")
            def wrong(q,p):
                return {"available":True,"selected":"맥주","ambiguous":False,
                        "conflicting_choices":[],"back_translation":"Bia"}
            result=verify_quiz(quiz(),store,dictionary=FakeDictionary(),reviewer=wrong)
            self.assertIn("Hỏi ngược không chọn đáp án gốc",result["issues"])
            self.assertEqual(result["outcome"],"needs_review")
    def test_legacy_auto_approved_without_qa_can_be_rechecked(self):
        with tempfile.TemporaryDirectory() as td:
            service=Service(td)
            qid=service.store.insert_question(quiz(),provider="gemini")
            service.store.auto_approve([qid])  # Pre-v1.2 state without QA.
            self.assertEqual(service.store.get_question_qa(qid)["outcome"],"not_checked")
            def verify(qid,mode="strict"):
                service.store.set_question_qa(qid,"verified",
                    {"tier":"dictionary+blind_ai","issues":[]})
                return {"id":qid,"status":"verified","issues":[]}
            service.verify_generated=verify
            out=service.verify_pending_questions(limit=8)
            self.assertEqual(out["verified"],1)
            self.assertEqual(service.store.list_questions()[0]["qa_outcome"],"verified")

    def test_qa_outcome_independent_from_render_status(self):
        with tempfile.TemporaryDirectory() as td:
            store=Store(Path(td)/"test.sqlite3")
            qid=store.insert_question(quiz(),provider="gemini")
            self.assertEqual(store.list_questions()[0]["qa_outcome"],"not_checked")
            store.set_question_qa(qid,"verified",{"tier":"dictionary+blind_ai","issues":[]})
            good=store.list_questions()[0]
            self.assertEqual(good["status"],"auto_approved")
            self.assertEqual(good["qa_outcome"],"verified")
            self.assertEqual(good["qa_report"]["tier"],"dictionary+blind_ai")
            store.update_answer_details(qid,{"wrong3_meaning":"Sữa tươi"})
            stale=store.list_questions()[0]
            self.assertEqual(stale["status"],"draft")
            self.assertEqual(stale["qa_outcome"],"stale")

class ThumbnailTests(unittest.TestCase):
    def test_thumbnail_lives_next_to_mp4_and_uses_opening_topic(self):
        from studio.renderer import font
        try:font(20,True,True)
        except RuntimeError:self.skipTest("Korean font not available in CI")
        with tempfile.TemporaryDirectory() as td:
            mp4=Path(td)/"THSE_001.mp4";mp4.write_bytes(b"example")
            config=Production(td).settings()
            file=ensure_thumbnail(mp4,"Giáng sinh ở Hàn Quốc","TOPIK 1",
                                  "sejong",config)
            self.assertEqual(file,thumbnail_path(mp4))
            self.assertTrue(file.is_file())
            from PIL import Image
            with Image.open(file) as img:self.assertEqual(img.size,(1080,1920))
    def test_youtube_thumbnail_retry_is_independent_of_upload(self):
        with tempfile.TemporaryDirectory() as td:
            store=Store(Path(td)/"db.sqlite3")
            mp4=Path(td)/"video.mp4";mp4.write_bytes(b"fake")
            thumbnail_path(mp4).write_bytes(b"JPG")
            future=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat()
            jid=store.schedule_outbound(mp4,"youtube","Title","Caption",future)
            with store.db() as c:
                c.execute("UPDATE outbound_posts SET publish_at=? WHERE id=?",
                          ((datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat(),jid))
            scheduler=Scheduler(store)
            with patch.object(scheduler.youtube,"upload",return_value="abc123456789") as upload,\
                 patch.object(scheduler.youtube,"processing_status",return_value="succeeded"),\
                 patch.object(scheduler.youtube,"set_thumbnail",return_value=True) as thumb:
                scheduler.tick()
            self.assertEqual(upload.call_count,1)
            self.assertEqual(thumb.call_count,1)
            self.assertEqual(store.list_youtube_thumbnails()[0]["status"],"completed")
            self.assertEqual(store.list_outbound()[0]["status"],"submitted")
            # A retry only sends the thumbnail; it never invokes upload again.
            store.enqueue_youtube_thumbnail(mp4,"new123456789")
            with patch.object(scheduler.youtube,"processing_status",return_value="processing"),\
                 patch.object(scheduler.youtube,"upload",side_effect=AssertionError("reupload")):
                scheduler.tick()
            pending=next(x for x in store.list_youtube_thumbnails() if x["video_id"]=="new123456789")
            self.assertEqual(pending["status"],"pending")

class UIContract(unittest.TestCase):
    def test_qa_and_cover_controls_are_present(self):
        html=(Path(__file__).resolve().parents[1]/"studio"/"ui.html").read_text("utf8")
        for name in ("autoQaMode","testKrdict","bulkVerifyAI","crossThumbnail",
                     "crossThumbDownload","generateOldThumbnails","keyKrdict"):
            self.assertIn('id="'+name+'"',html)

if __name__=="__main__":unittest.main()
