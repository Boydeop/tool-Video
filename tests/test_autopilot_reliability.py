"""Regression: v1.2.0 66%-for-30-minutes Autopilot stall.

All network calls mocked. The tests cover per-topic request budgets,
explicit stage reporting, cooperative cancellation and file preservation.
"""
from __future__ import annotations
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from studio.ai import post
from studio.autopilot import Plan,run_pipeline
from studio.exports import ExportQueue
from studio.run_limits import (topic_budget,network_timeout,check_budget,
                               BudgetExpired,RunCancelled)
from studio.service import Service


class RunDeadlineTests(unittest.TestCase):
    def test_per_request_network_time_does_not_exceed_remaining_budget(self):
        with topic_budget(.7):
            self.assertGreater(network_timeout(20,70),0)
            self.assertLessEqual(network_timeout(20,70),.7)
        self.assertEqual(network_timeout(20,70),70)

    def test_expired_budget_and_cooperative_cancel(self):
        with patch("studio.run_limits.time.monotonic",side_effect=[100.0,101.1]):
            with self.assertRaises(BudgetExpired):
                with topic_budget(1):
                    check_budget()
        signal=threading.Event()
        with topic_budget(210,signal):
            signal.set()
            with self.assertRaises(RunCancelled):
                check_budget()

    def test_ai_http_transport_bounded_inside_autopilot(self):
        request_timeouts=[]
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,n):return b'{"ok":true}'
        def urlopen(req,timeout):
            request_timeouts.append(timeout)
            return Response()
        with patch("studio.ai.urllib.request.urlopen",side_effect=urlopen):
            with topic_budget(30):
                value=post("gemini","https://example.invalid",{},{"x-goog-api-key":"hidden"})
        self.assertTrue(value["ok"])
        self.assertLessEqual(request_timeouts[0],20)

class PipelineStallTests(unittest.TestCase):
    def test_budget_expiry_returns_reviewable_partial_not_hang(self):
        with tempfile.TemporaryDirectory() as folder:
            service=Service(folder)
            def expired(*args,**kwargs):
                raise BudgetExpired("Giới hạn chờ API trong bài kiểm thử")
            service.generate=expired
            updates=[]
            got=run_pipeline(service,Plan.parse({"videos":1,"per_video":3}),
                             lambda percent,phase:updates.append((percent,phase)))
            self.assertEqual(got["rendered"],0)
            self.assertEqual(got["topics"][0]["status"],"needs_review")
            self.assertIn("Giới hạn chờ",got["topics"][0]["reason"])
            self.assertTrue(any("AI tạo câu" in phase for _,phase in updates))
            self.assertTrue(any("quá thời gian" in phase for _,phase in updates))
            self.assertTrue(all(0<=fraction<=100 for fraction,_ in updates))

    def test_cancellation_finishes_stored_topic_record(self):
        with tempfile.TemporaryDirectory() as folder:
            service=Service(folder)
            signal=threading.Event()
            def mid_call(*args,**kwargs):
                signal.set()
                raise RunCancelled("Yêu cầu dừng")
            service.generate=mid_call
            snapshots=[]
            got=run_pipeline(service,Plan.parse({"videos":3,"per_video":3}),
                lambda percent,phase:None,on_result=lambda r:snapshots.append(r),
                cancel=signal)
            self.assertEqual(got["rendered"],0)
            self.assertEqual(len(got["topics"]),1)
            self.assertEqual(got["topics"][0]["status"],"cancelled")
            self.assertTrue(snapshots)

class QueueCancelTests(unittest.TestCase):
    def test_cancel_only_autopilot_and_keep_completed_mp4(self):
        with tempfile.TemporaryDirectory() as folder:
            service=Service(folder)
            queue=ExportQueue(service)
            entered=threading.Event()
            video=Path(folder)/"already_rendered.mp4"
            video.write_bytes(b"KEEP_THIS_MP4")
            def faked_pipeline(service,plan,update,on_result=None,cancel=None,topics=None):
                entered.set()
                cancel.wait(3)
                assert cancel.is_set()
                from studio.quiz import Quiz
                from studio.service import _PRODUCTION
                q=Quiz(topic='Test',question='Nước',correct='물',wrong1='봄',wrong2='여름',wrong3='가을')
                qid=service.store.insert_question(q,production_task=_PRODUCTION.get()['id'])
                service.store.approve([qid]);service.store.mark_rendered([qid],video)
                item={"topic":"Test","status":"rendered","video":str(video)}
                if on_result:on_result([item])
                return {"videos":[str(video)],"topics":[item],"rendered":1,
                        "needs_review":[]}
            with patch("studio.batch_runner.run_pipeline",side_effect=faked_pipeline):
                task=queue.start_autopilot({"videos":2,"per_video":3})
                self.assertTrue(entered.wait(3))
                reply=queue.cancel_autopilot(task["job_id"])
                self.assertEqual(reply["status"],"stopping")
                for _ in range(100):
                    data=queue.status(task["job_id"])
                    if data["status"]=="cancelled":break
                    time.sleep(.01)
            self.assertEqual(data["status"],"cancelled")
            self.assertEqual(data["videos"],[str(video)])
            self.assertEqual(video.read_bytes(),b"KEEP_THIS_MP4")
            self.assertGreaterEqual(data["elapsed_seconds"],0)
            self.assertGreaterEqual(data["phase_elapsed_seconds"],0)
            self.assertEqual(service.store.list_production_runs()[0]["status"],"paused")
            with self.assertRaises(ValueError):queue.cancel_autopilot(task["job_id"])

class UiHotfixTests(unittest.TestCase):
    def test_progress_and_safe_cancel_actions_present(self):
        html=(Path(__file__).resolve().parents[1]/"studio"/"ui.html").read_text("utf8")
        for item in ('id="autoCancel"','id="autoTiming"',
                     '"/api/autopilot/cancel"',"phase_elapsed_seconds"):
            self.assertIn(item,html)

if __name__=="__main__":
    unittest.main()
