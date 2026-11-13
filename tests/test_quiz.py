import tempfile
import unittest
from pathlib import Path
from studio.quiz import Quiz, daily_plan, fingerprint
from studio.database import Store, DuplicateQuestion

def example(question="Xin chào",correct="안녕하세요"):
    return Quiz(topic="Giao tiếp",question=question,correct=correct,
                wrong1="감사합니다",wrong2="죄송합니다",wrong3="안녕히 가세요")

class QuizTests(unittest.TestCase):
    def test_normalization(self):
        self.assertEqual(fingerprint(" Xin   chào! "),fingerprint("xin chào"))
    def test_invalid_choices(self):
        q=example()
        self.assertRaises(ValueError,Quiz.parse,{**q.to_dict(),"wrong1":q.correct})
    def test_duplicate_persists(self):
        with tempfile.TemporaryDirectory() as root:
            store=Store(Path(root)/"db.sqlite")
            store.insert_question(example())
            self.assertRaises(DuplicateQuestion,store.insert_question,example("Xin chào!"))
            self.assertEqual(Store(Path(root)/"db.sqlite").count_questions(),1)
    def test_daily_plan_deterministic(self):
        from datetime import date
        self.assertEqual(daily_plan(date(2026,11,13)),daily_plan(date(2026,11,13)))
    def test_approval(self):
        with tempfile.TemporaryDirectory() as root:
            store=Store(Path(root)/"db.sqlite")
            qid=store.insert_question(example())
            self.assertRaises(ValueError,store.get_questions,[qid],"approved")
            store.approve([qid])
            self.assertEqual(len(store.get_questions([qid],"approved")),1)
    def test_schedule_requires_tz(self):
        with tempfile.TemporaryDirectory() as root:
            store=Store(Path(root)/"db.sqlite")
            video=Path(root)/"test.mp4"; video.write_bytes(b"fake")
            self.assertRaises(ValueError,store.add_job,str(video),"caption","2030-01-01T08:00:00")

if __name__=="__main__": unittest.main()
