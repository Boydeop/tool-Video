"""Verify new model endpoints without network calls or real API credentials."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from studio.service import Service
from studio.webapp import create_server

class ModelEndpointTests(unittest.TestCase):
    def test_model_selection_visible_in_status_and_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"GEMINI_MODEL":""}):
                service = Service(tmp)
                server = create_server(service, port=0)
                th = threading.Thread(target=server.serve_forever, daemon=True)
                th.start()
                base = "http://127.0.0.1:" + str(server.server_address[1])
                def invoke(route, payload=None):
                    data = None if payload is None else json.dumps(payload).encode()
                    req = urllib.request.Request(base+route, data=data,
                        headers={"Content-Type":"application/json"} if data else {})
                    with urllib.request.urlopen(req,timeout=3) as response:
                        return json.load(response)
                try:
                    with patch("studio.webapp.list_models", return_value={
                        "provider":"gemini","key_position":1,"count":1,
                        "models":[{"id":"gemini-3.5-flash-lite",
                                   "label":"Gemini Flash Lite","free":None}]
                    }):
                        models = invoke("/api/models?provider=gemini")
                    self.assertEqual(models["models"][0]["id"],"gemini-3.5-flash-lite")
                    selected = invoke("/api/models/select",{
                        "provider":"gemini","model":"gemini-3.5-flash-lite"})
                    self.assertEqual(selected["model"],"gemini-3.5-flash-lite")
                    self.assertEqual(invoke("/api/status")["models"]["gemini"],
                                     "gemini-3.5-flash-lite")
                    with patch("studio.webapp.test_model", return_value={
                        "provider":"gemini","model":"gemini-3.5-flash-lite",
                        "key_position":1,"status":"ok","message":"ok"
                    }) as test:
                        result=invoke("/api/models/test",{
                            "provider":"gemini","model":"gemini-3.5-flash-lite",
                            "key_position":1})
                        self.assertEqual(result["status"],"ok")
                        self.assertEqual(test.call_args.args[2],1)
                    with self.assertRaises(urllib.error.HTTPError) as err:
                        invoke("/api/models/select",{"provider":"gemini","model":"http://bad"})
                    self.assertEqual(err.exception.code,400)
                finally:
                    server.shutdown()
                    server.server_close()
                    th.join(timeout=4)

if __name__=="__main__":
    unittest.main()
