"""Meta Reel upload mock: title passed through; no real Page publication."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from studio.publisher import publish_reel

class FakeResponse:
    def __init__(self,payload):self.data=io.BytesIO(json.dumps(payload).encode())
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,*args):return self.data.read(*args)

class PublisherTests(unittest.TestCase):
    def test_publishes_title_and_description_via_meta_api(self):
        requests=[]
        def fake_open(request,timeout=0):
            requests.append(request)
            if len(requests)==1:
                return FakeResponse({"video_id":"12345678",
                    "upload_url":"https://rupload.facebook.com/video-upload/v24.0/12345678"})
            if len(requests)==2:return FakeResponse({"success":True})
            return FakeResponse({"success":True})
        with tempfile.TemporaryDirectory() as td:
            video=Path(td)/"test.mp4";video.write_bytes(b"fake mp4 bytes")
            with patch("studio.publisher.credentials",return_value=("123456","FAKE_TOKEN")):
                with patch("studio.publisher.urllib.request.urlopen",side_effect=fake_open):
                    vid=publish_reel(video,"Mô tả #TiengHan","Tiêu đề SEO")
            self.assertEqual(vid,"12345678")
            self.assertEqual(len(requests),3)
            from urllib.parse import parse_qs
            form=parse_qs(requests[2].data.decode("utf-8"))
            self.assertEqual(form["title"][0],"Tiêu đề SEO")
            self.assertNotIn("FAKE_TOKEN",str(vid))

if __name__=="__main__":unittest.main()
