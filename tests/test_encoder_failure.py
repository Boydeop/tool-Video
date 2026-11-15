"""A noisy encoder must fail promptly rather than deadlock both pipes."""
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


class EncoderFailureTests(unittest.TestCase):
    def test_large_stderr_does_not_block_frame_writer(self):
        with tempfile.TemporaryDirectory() as td:
            script=Path(td)/'noisy_encoder.py'
            script.write_text('import sys\nsys.stderr.write("X"*1000000)\nsys.stderr.flush()\nsys.stdin.buffer.read()\nsys.exit(1)\n')
            driver=textwrap.dedent('''
                from pathlib import Path
                import subprocess,sys
                from unittest.mock import patch
                from PIL import Image
                from studio.renderer import render
                from studio.quiz import Quiz
                real_popen=subprocess.Popen
                def start(args,**kwargs):
                    return real_popen([sys.executable,*args],**kwargs)
                q=Quiz(topic='Test',question='Nước',correct='물',wrong1='불',wrong2='차',wrong3='맥주')
                with patch('studio.renderer.binary',return_value=sys.argv[1]), \\
                     patch('studio.renderer.subprocess.Popen',side_effect=start), \\
                     patch('studio.renderer.draw_intro',return_value=Image.new('RGB',(32,32))), \\
                     patch('studio.renderer.draw_frame',return_value=Image.new('RGB',(32,32))):
                    try:
                        render([q],Path(sys.argv[1]).with_suffix('.mp4'),think=4,reveal=1.5,transition=.3,intro=.5,sfx=False)
                    except RuntimeError as exc:
                        assert 'FFmpeg render failed:' in str(exc)
                        print('Encoder failure handled')
                    else:raise AssertionError('No encoder error')
            ''')
            result=subprocess.run([sys.executable,'-c',driver,str(script)],
                cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('Encoder failure handled',result.stdout)


if __name__=='__main__':unittest.main()
