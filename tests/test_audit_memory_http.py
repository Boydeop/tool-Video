"""HTTP boundary and QA display regressions, no live API calls."""
from dataclasses import replace
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from studio.review import verify_quiz
from studio.service import Service
from studio.webapp import create_server
from test_krdict_review_thumbnails import quiz, FakeDictionary, reviewer


class AuditHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.service=Service(self.temp.name)
        self.server=create_server(self.service,port=0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=5)
        self.temp.cleanup()

    def request(self,method,path,body=None,headers=None):
        connection=http.client.HTTPConnection(*self.server.server_address,timeout=5)
        try:
            connection.request(method,path,body=body,headers=headers or {})
            response=connection.getresponse()
            return response.status,response.read()
        finally:connection.close()

    def test_rebinding_and_cross_origin_reads_and_writes_are_rejected(self):
        for method in ('GET','POST'):
            for headers in ({'Host':'attacker.example:8765'},
                            {'Origin':'https://attacker.example'},
                            {'Origin':'null'}):
                with self.subTest(method=method,headers=headers):
                    status,_=self.request(method,'/api/status',headers=headers)
                    self.assertEqual(status,403)

    def test_simple_form_cannot_trigger_mutation(self):
        qid=self.service.store.insert_question(quiz())
        status,_=self.request('POST','/api/approve',json.dumps({'ids':[qid]}),
                              {'Content-Type':'text/plain'})
        self.assertEqual(status,400)
        self.assertEqual(self.service.store.list_questions()[0]['status'],'draft')

    def test_memory_status_and_backup_survive_purge(self):
        store=self.service.store;qid=store.insert_question(quiz())
        store.trash_questions([qid]);store.empty_question_trash()
        status,body=self.request('GET','/api/memory/status')
        self.assertEqual(status,200);self.assertEqual(json.loads(body)['answers'],1)
        status,body=self.request('GET','/api/memory/export')
        self.assertEqual(status,200)
        backup=Path(self.temp.name)/'download.jsonl';backup.write_bytes(body)
        self.assertEqual(store.import_memory(backup)['added'],0)
        status,response=self.request('POST','/api/memory/import',
            json.dumps({'text':body.decode('utf-8')}),{'Content-Type':'application/json'})
        self.assertEqual(status,200)
        self.assertEqual(json.loads(response)['added'],0)
        status,_=self.request('POST','/api/memory/import',
            json.dumps({'text':'not a backup'}),{'Content-Type':'application/json'})
        self.assertEqual(status,400)
        status,body=self.request('GET','/api/status')
        self.assertEqual(status,200)
        self.assertEqual(json.loads(body)['question_memory']['questions_seen'],1)


class DisplayMeaningTests(unittest.TestCase):
    def test_wrong_displayed_meaning_fails_even_with_correct_blind_choice(self):
        for mode in ('strict','assisted'):
            for field in ('meaning','wrong1_meaning','wrong2_meaning','wrong3_meaning'):
                with self.subTest(mode=mode,field=field):
                    judge=Mock(side_effect=reviewer)
                    report=verify_quiz(replace(quiz(),**{field:'Máy bay'}),Mock(),mode=mode,
                                       dictionary=FakeDictionary(),reviewer=judge)
                    self.assertEqual(report['outcome'],'needs_review')
                    self.assertTrue(any('Nghĩa hiển thị' in issue for issue in report['issues']))
                    if mode=='strict':judge.assert_not_called()


if __name__=='__main__':unittest.main()
