"""Voice fallback behavior is fully mocked: no online TTS or user secrets."""
import os
import tempfile
import threading
import unittest
import urllib.parse
import urllib.request
import json
from pathlib import Path
from unittest.mock import patch

from studio import voice
from studio.service import Service
from studio.webapp import create_server

def save_fake_mp3(text,lang,path):
    Path(path).write_bytes(b"FAKEAUDIO"*32)

class VoiceFallbackTests(unittest.TestCase):
    def test_fallback_when_edge_no_audio(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(voice,"readiness",return_value={"edge":True,"google":True}),\
                 patch.object(voice,"_speak_edge",side_effect=RuntimeError("NoAudioReceived")) as edge,\
                 patch.object(voice,"_speak_google",side_effect=save_fake_mp3) as google,\
                 patch.object(voice.time,"sleep"):
                output=voice.synthesize("안녕하세요",voice.KO_VOICE,Path(temp),"auto")
                self.assertTrue(output.is_file())
                self.assertGreater(output.stat().st_size,100)
                self.assertEqual(edge.call_count,4) # 2 Korean voices, 2 attempts each
                google.assert_called_once()
                # Successful audio is cached and not fetched again.
                self.assertEqual(output,voice.synthesize("안녕하세요",voice.KO_VOICE,Path(temp),"auto"))
                google.assert_called_once()

    def test_edge_only_never_masks_error(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(voice,"readiness",return_value={"edge":True,"google":True}),\
                 patch.object(voice,"_speak_edge",side_effect=RuntimeError("NoAudioReceived")) as edge,\
                 patch.object(voice,"_speak_google") as google,\
                 patch.object(voice.time,"sleep"):
                with self.assertRaises(voice.SpeechError):
                    voice.synthesize("Xin chào",voice.VI_VOICE,temp,"edge")
                self.assertEqual(edge.call_count,4)
                google.assert_not_called()

    def test_google_direct_and_input_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(voice,"readiness",return_value={"edge":False,"google":True}),\
                 patch.object(voice,"_speak_google",side_effect=save_fake_mp3) as google:
                path=voice.test_speech("vi","google",temp)
                self.assertTrue(path.is_file())
                self.assertEqual(google.call_args.args[1],"vi")
            with self.assertRaises(ValueError):voice.test_speech("xx","auto",temp)
            with self.assertRaises(ValueError):voice.synthesize("Hi",voice.VI_VOICE,temp,"bad")

    def test_voice_sample_endpoint_and_status(self):
        with tempfile.TemporaryDirectory() as temp:
            service=Service(temp)
            server=create_server(service,port=0)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            base="http://127.0.0.1:"+str(server.server_address[1])
            try:
                def fake_test(language,engine,cache):
                    path=Path(cache)/("a"*64+".mp3")
                    path.parent.mkdir(parents=True,exist_ok=True)
                    path.write_bytes(b"FAKEAUDIO"*32)
                    return path
                with patch("studio.webapp.test_speech",side_effect=fake_test):
                    req=urllib.request.Request(base+"/api/voice/test",
                        data=json.dumps({"language":"ko","engine":"auto"}).encode(),
                        headers={"Content-Type":"application/json"},method="POST")
                    with urllib.request.urlopen(req,timeout=4) as r:
                        resp=json.load(r)
                self.assertEqual(resp["language"],"ko")
                with urllib.request.urlopen(base+resp["audio_url"],timeout=4) as r:
                    self.assertEqual(r.headers["Content-Type"],"audio/mpeg")
                    self.assertGreater(len(r.read()),100)
                with urllib.request.urlopen(base+"/api/status",timeout=4) as r:
                    status=json.load(r)
                self.assertIn("tts_engines",status)
            finally:
                server.shutdown();server.server_close();thread.join(timeout=4)

if __name__=="__main__":
    unittest.main()
