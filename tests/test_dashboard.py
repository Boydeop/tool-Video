"""No cloud calls. Exercise the real HTTP UI and TXT key import."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
import tempfile
import threading
import urllib.parse
import unittest
from unittest.mock import patch
import urllib.request

from studio.quiz import TOPICS
from studio.service import Service
from studio.webapp import create_server

class DashboardTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is not installed")
    def test_dashboard_javascript_syntax(self):
        text=Path("studio/ui.html").read_text(encoding="utf-8")
        match=re.search(r"<script>([\s\S]*?)</script>", text)
        self.assertIsNotNone(match)
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"ui.js"
            path.write_text(match.group(1),encoding="utf-8")
            result=subprocess.run(["node","--check",str(path)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_dashboard_topic_picker_and_secret_safe_import(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ,{
                "GEMINI_API_KEYS":"","GEMINI_API_KEY":"",
                "GROQ_API_KEYS":"","GROQ_API_KEY":""}):
            store=Service(td)
            server=create_server(store,port=0)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            host,port=server.server_address
            root=f"http://{host}:{port}"
            def request(path,payload=None):
                data=None if payload is None else json.dumps(payload).encode()
                req=urllib.request.Request(root+path,data=data,
                    headers={"Content-Type":"application/json"} if data else {})
                with urllib.request.urlopen(req,timeout=4) as resp:
                    return resp.read().decode("utf8")
            try:
                home=request("/")
                self.assertIn("Chọn file .TXT",home)
                self.assertIn("Đổi chủ đề khác",home)
                self.assertIn("function showPanel",home)
                first=json.loads(request("/api/topic/random"))["topic"]
                self.assertIn(first,TOPICS)
                second=json.loads(request("/api/topic/random?exclude="+urllib.parse.quote(first)))["topic"]
                self.assertNotEqual(first,second)
                result=json.loads(request("/api/keys/import",{
                    "provider":"gemini","text":"AIzaEXAMPLE00001\nAIzaEXAMPLE00002"}))
                self.assertEqual(result["saved"]["gemini"],2)
                self.assertNotIn("AIzaEXAMPLE",json.dumps(result))
                status=json.loads(request("/api/status"))
                self.assertEqual(status["providers"]["gemini"]["keys"],2)
                self.assertIn("videos",status)
            finally:
                server.shutdown();server.server_close();thread.join(timeout=4)

if __name__=="__main__":unittest.main()
