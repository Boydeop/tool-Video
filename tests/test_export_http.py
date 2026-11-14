"""HTTP export queue and data enrichment routes never use external services."""
import json
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from studio.service import Service
from studio.quiz import Quiz
from studio.webapp import create_server

def quiz():
    return Quiz(topic="Đồ uống",question="Trà",correct="차",wrong1="물",
                wrong2="맥주",wrong3="콜라",romanization="cha",meaning="Trà")

class ExportHTTPTests(unittest.TestCase):
    def test_draft_trash_restore_and_durable_resume_routes(self):
        with tempfile.TemporaryDirectory() as td:
            service=Service(td);ident=service.store.insert_question(quiz(),provider='gemini')
            server=create_server(service,port=0)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base='http://127.0.0.1:'+str(server.server_address[1])
            def request(route,data=None):
                payload=None if data is None else json.dumps(data).encode()
                with urllib.request.urlopen(urllib.request.Request(base+route,data=payload,
                    headers={'Content-Type':'application/json'}),timeout=6) as response:return json.load(response)
            def wait(jid):
                for _ in range(300):
                    state=request('/api/render/progress?job_id='+jid)
                    if state['status']=='blocked' and not server.export_queue.is_running():return state
                    time.sleep(.01)
                self.fail('Queue never reached blocked checkpoint')
            try:
                self.assertEqual(request('/api/questions/manage',{'action':'trash','ids':[ident]})['trashed'],1)
                self.assertEqual(len(request('/api/status')['trash_questions']),1)
                self.assertEqual(request('/api/questions/manage',{'action':'restore','ids':[ident]})['restored'],1)
                with patch('studio.batch_runner.BatchRunner._produce',return_value={
                        'topics':[{'blocked':True,'reason':'fixture missing credentials'}]}) as process:
                    jid=request('/api/autopilot/start',{'videos':100,'per_video':1})['job_id']
                    wait(jid)
                    self.assertEqual(len(service.store.batch_tasks(jid)),100)
                    self.assertEqual(request('/api/autopilot/resume',{'job_id':jid})['job_id'],jid)
                    wait(jid)
                    self.assertEqual(process.call_count,2)
                self.assertEqual(len(request('/api/status')['questions']),1)
            finally:
                server.shutdown();server.server_close();thread.join(timeout=4)

    def test_export_progress_route_and_detail_update(self):
        with tempfile.TemporaryDirectory() as td:
            service=Service(td)
            ident=service.store.insert_question(quiz())
            server=create_server(service,port=0)
            thread=threading.Thread(target=server.serve_forever,daemon=True)
            thread.start()
            base="http://127.0.0.1:"+str(server.server_address[1])
            def request(uri,data=None):
                payload=json.dumps(data).encode() if data is not None else None
                with urllib.request.urlopen(urllib.request.Request(
                    base+uri,data=payload,
                    headers={"Content-Type":"application/json"} if payload else {}),
                    timeout=6) as response:
                    return json.load(response)
            try:
                updated=request("/api/answers/save",{"id":ident,"details":{
                    "wrong1_meaning":"Nước","wrong1_romanization":"mul",
                    "wrong2_meaning":"Bia","wrong2_romanization":"maekju",
                    "wrong3_meaning":"Cola","wrong3_romanization":"kolla"}})
                self.assertEqual(updated["status"],"draft")
                service.store.approve([ident])
                with patch.object(service,"render_approved",return_value="exported.mp4") as render:
                    job=request("/api/render/start",{"ids":[ident],"per_video":1,
                        "theme":"hanji","with_voice":False})
                    for _ in range(300):
                        state=request("/api/render/progress?job_id="+job["job_id"])
                        if state["status"] in ("completed","failed"):break
                        time.sleep(.01)
                    self.assertEqual(state["status"],"completed",state.get("error"))
                    self.assertEqual(state["percent"],100)
                    render.assert_called_once()
                status=request("/api/status")
                self.assertIn("metadata_artist",status["production"])
                self.assertIn("logo_x",status["production"])
            finally:
                server.shutdown();server.server_close();thread.join(timeout=4)

class UIChecks(unittest.TestCase):
    def test_ui_has_all_required_controls(self):
        ui=(Path(__file__).resolve().parents[1]/"studio"/"ui.html").read_text(encoding="utf-8")
        for control in ["renderProgressBar","renderProgressPercent","logoSprite",
                        "answerEditor","metadataCopyright","overwriteMetadata",
                        "saveAnswerEditor","trashSelected","previewImage"]:
            self.assertIn('id="'+control+'"',ui)

    @unittest.skipUnless(shutil.which("node"),"Node.js is unavailable")
    def test_javascript_syntax(self):
        ui=(Path(__file__).resolve().parents[1]/"studio"/"ui.html").read_text(encoding="utf-8")
        script=re.search(r"<script[^>]*>([\s\S]+?)</script>",ui)
        self.assertIsNotNone(script)
        with tempfile.TemporaryDirectory() as td:
            file=Path(td)/"studio.js"
            file.write_text(script.group(1),encoding="utf-8")
            result=subprocess.run(["node","--check",str(file)],capture_output=True,
                                   text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
