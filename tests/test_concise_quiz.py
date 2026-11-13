"""Regression tests for concise quiz narration and audio-first countdown.

Never use real API keys, providers or external TTS in CI.
"""
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import ImageChops
from studio.quiz import Quiz
from studio.romanization import approximate_romanization
from studio.timeline import build_scenes,duration
from studio import voice
from studio import renderer

def example():
    return Quiz(topic="Công sở",question="Từ tiếng Hàn nào có nghĩa là 'nhân viên công ty'?",
        correct="회사원",wrong1="학생",wrong2="의사",wrong3="선생님",
        clue="Nhân viên công ty",meaning="Nhân viên công ty",
        romanization="hoesawon",level="TOPIK 1")

class ConciseAudioFirstTests(unittest.TestCase):
    def test_legacy_question_is_shortened_without_db_migration(self):
        q=Quiz(topic="Công sở",
            question="Từ tiếng Hàn nào có nghĩa là 'nhân viên công ty'?",
            correct="회사원",wrong1="학생",wrong2="의사",wrong3="선생님")
        self.assertEqual(q.short_clue,"Nhân viên công ty")
        self.assertEqual(q.vietnamese_meaning,"Nhân viên công ty")
        self.assertEqual(approximate_romanization("회사원"),"hoesawon")
        self.assertEqual(approximate_romanization("전화"),"jeonhwa")

    def test_new_fields_round_trip_and_old_payloads_still_load(self):
        q=example()
        parsed=Quiz.parse(q.to_dict())
        self.assertEqual(parsed.clue,"Nhân viên công ty")
        self.assertEqual(parsed.romanization,"hoesawon")
        self.assertEqual(parsed.meaning,"Nhân viên công ty")
        legacy=q.to_dict()
        legacy.pop("clue");legacy.pop("meaning")
        older=Quiz.parse(legacy)
        self.assertEqual(older.short_clue,"Nhân viên công ty")

    def test_timeline_does_not_count_during_reading(self):
        scene=build_scenes(1,think=5.5,reveal=2.8,transition=.8,
                          intro=1.5,fps=24,prompt_lengths=[3.4],
                          answer_lengths=[3.1])[0]
        start_of_voice=scene.start+.18
        self.assertGreaterEqual(scene.countdown_start-start_of_voice,3.4+.3)
        self.assertAlmostEqual(scene.answer_start-scene.countdown_start,5.5,places=4)
        self.assertGreaterEqual(scene.answer_duration,3.1)
        self.assertAlmostEqual(duration([scene]),scene.end,places=4)
        self.assertGreater(scene.end,1.5+5.5+2.8+.8)

    def test_voice_events_wait_for_real_question_clip_length(self):
        q=example()
        with tempfile.TemporaryDirectory() as temp:
            vi=Path(temp)/"vi.mp3";ko=Path(temp)/"ko.mp3"
            vi.write_bytes(b"fake mp3");ko.write_bytes(b"fake mp3")
            with patch("studio.voice.synthesize",side_effect=[vi,ko]) as synth,\
                 patch("studio.voice.audio_duration",side_effect=[2.8,1.1]):
                events,scenes=voice.prepare_events([q],temp,5.5,2.8,
                                    intro=1.5,transition=.8,return_scenes=True)
            self.assertEqual(synth.call_args_list[0].args[0],"Nhân viên công ty")
            self.assertEqual(synth.call_args_list[1].args[0],"회사원")
            scene=scenes[0]
            self.assertEqual(len(events),2)
            self.assertLess((events[0][1]/1000)+2.8,scene.countdown_start)
            self.assertAlmostEqual(events[1][1]/1000,scene.answer_start+.18,places=2)

    @unittest.skipUnless(any(Path(x).exists() for x in renderer.KOREAN_PATHS) and
                         any(Path(x).exists() for x in renderer.VIETNAMESE_REGULAR),
                         "Needs Unicode font coverage")
    def test_three_screen_stages_are_distinct(self):
        q=example()
        a=renderer.draw_frame(q,1,1,"hanji",elapsed=.6,prompt_duration=3.0)
        b=renderer.draw_frame(q,1,1,"hanji",elapsed=3.5,prompt_duration=3.0)
        c=renderer.draw_frame(q,1,1,"hanji",elapsed=8.7,prompt_duration=3.0)
        self.assertIsNotNone(ImageChops.difference(a,b).getbbox())
        self.assertIsNotNone(ImageChops.difference(b,c).getbbox())

if __name__=="__main__":
    unittest.main()
