import shutil
import tempfile
import unittest
from pathlib import Path
from studio.quiz import Quiz

class RendererTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg"),"FFmpeg is not installed")
    def test_mp4_export(self):
        from studio.renderer import render
        q=Quiz(topic="Chào hỏi",question="Xin chào",correct="안녕하세요",
               wrong1="감사합니다",wrong2="죄송합니다",wrong3="안녕히 가세요")
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/"hello.mp4"
            render([q],output,theme="hanji",width=540,fps=24,think=4,reveal=1.5,transition=.3,intro=.5,sfx=True)
            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size,1000)

if __name__=="__main__":unittest.main()
