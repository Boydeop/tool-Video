"""Local API integration checks without external Meta or model requests."""
import base64
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import patch

from PIL import Image
from studio.service import Service
from studio.quiz import Quiz
from studio.webapp import create_server

class ProductionHttpTests(unittest.TestCase):
    def test_settings_logo_preview_and_page_forms(self):
        with tempfile.TemporaryDirectory() as td:
            service=Service(td)
            server=create_server(service,port=0)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            base="http://127.0.0.1:"+str(server.server_address[1])
            def invoke(path,body=None):
                payload=json.dumps(body).encode() if body is not None else None
                req=urllib.request.Request(base+path,data=payload,
                    headers={"Content-Type":"application/json"} if payload else {})
                with urllib.request.urlopen(req,timeout=15) as response:
                    return response.headers, response.read()
            try:
                headers,data=invoke("/api/status")
                status=json.loads(data)
                self.assertIn("production",status)
                self.assertIn("batches",status)
                self.assertIn("facebook",status)
                _,data=invoke("/api/production/settings",{
                    "think":8,"reveal":3,"transition":1,
                    "export_dir":str(Path(td)/"export")})
                self.assertEqual(json.loads(data)["think"],8)
                img=Image.new("RGBA",(70,50),(50,120,210,200))
                buf=io.BytesIO();img.save(buf,format="PNG")
                _,data=invoke("/api/logo/upload",{
                    "base64":base64.b64encode(buf.getvalue()).decode()})
                self.assertTrue(json.loads(data)["logo"])
                headers,data=invoke("/api/logo")
                self.assertEqual(headers["Content-Type"],"image/png")
                self.assertGreater(len(data),100)
                with patch("studio.webapp.connect_page",return_value={
                    "connected":True,"page_name":"Test Page","page_id":"12345"}):
                    _,data=invoke("/api/facebook/connect",{
                        "page_id":"12345","token":"FAKE_PAGE_TOKEN_SENSITIVE_VALUE"})
                self.assertNotIn(b"FAKE_PAGE_TOKEN",data)
                # No real SEO request; check API routes accept payload.
                with patch.object(service,"seo_video",return_value={
                    "title":"Mock SEO","caption":"Mock SEO content"}):
                    _,data=invoke("/api/seo",{"video_name":"dummy.mp4"})
                    self.assertEqual(json.loads(data)["title"],"Mock SEO")
                with patch.object(service,"preview_motion",return_value=Path("preview_abc.mp4")):
                    _,data=invoke("/api/preview/clip",{"id":"qid","theme":"hanji"})
                    self.assertIn(b"preview_abc.mp4",data)
            finally:
                server.shutdown();server.server_close();thread.join(timeout=5)

if __name__=="__main__":unittest.main()
