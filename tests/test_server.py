"""Regression checks for frozen, console-free Windows executables."""
import json
import logging
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.request
from unittest.mock import patch

from studio.service import Service
from studio.webapp import create_server

class LocalServerTests(unittest.TestCase):
    def test_no_stderr_still_returns_valid_http(self):
        with tempfile.TemporaryDirectory() as folder:
            server=create_server(Service(folder),port=0)
            host,port=server.server_address
            t=threading.Thread(target=server.serve_forever,daemon=True)
            t.start()
            try:
                # PyInstaller --windowed sets stderr to None. Previously
                # BaseHTTPRequestHandler.send_response() raised AttributeError.
                with patch.object(sys,"stderr",None):
                    with urllib.request.urlopen(f"http://{host}:{port}/",timeout=3) as r:
                        self.assertEqual(r.status,200)
                        html=r.read().decode("utf-8")
                        self.assertIn("TIẾNG HÀN SO EASY",html)
                    with urllib.request.urlopen(f"http://{host}:{port}/health",timeout=3) as r:
                        self.assertEqual(json.load(r)["status"],"ok")
                    with urllib.request.urlopen(f"http://{host}:{port}/api/status",timeout=3) as r:
                        data=json.load(r)
                        self.assertIn("questions",data)
                        self.assertIn("providers",data)
            finally:
                server.shutdown()
                server.server_close()
                t.join(timeout=5)

if __name__=="__main__":
    unittest.main()
