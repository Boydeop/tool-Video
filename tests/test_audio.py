"""No online TTS calls: a generated 440Hz tone tests mux + ffprobe."""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from studio.quiz import Quiz

class AudioTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),
                         "Needs FFmpeg and FFprobe")
    def test_audio_stream_in_mp4(self):
        from studio.renderer import render
        from studio.voice import mux_narration,audio_streams
        q=Quiz(topic="Chào hỏi",question="Xin chào",correct="안녕하세요",
               wrong1="감사합니다",wrong2="죄송합니다",wrong3="안녕히 가세요")
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            sample=root/"tone.wav"
            subprocess.run(["ffmpeg","-hide_banner","-loglevel","error","-y",
                "-f","lavfi","-i","sine=frequency=440:duration=0.6",
                str(sample)],check=True,timeout=30)
            silent=root/"silent.mp4"
            final=root/"spoken.mp4"
            render([q],silent,theme="hanji",fps=24,think=4,reveal=1.5,transition=.3,intro=.5,sfx=False)
            self.assertIn("aac",audio_streams(silent))
            mux_narration(silent,[(sample,50)],final,6.3)
            self.assertIn("aac",audio_streams(final))
            self.assertGreater(final.stat().st_size,1000)

if __name__=="__main__":unittest.main()
