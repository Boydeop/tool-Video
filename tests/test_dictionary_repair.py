"""Dictionary-backed repairs, atomic history and manual/background workflows.
All provider/dictionary calls are mocked; no live keys or publications.
"""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.request
from unittest.mock import Mock,patch
from studio.database import DuplicateQuestion,Store
from studio.exports import ExportQueue
from studio.quiz import Quiz
from studio.repair import repair_quiz,propose,EDITABLE
from studio.run_limits import BudgetExpired,RunCancelled
from studio.service import Service
from studio.webapp import create_server


def original():
    return Quiz(topic='Giáng sinh',question='Tuần lộc',clue='Tuần lộc',
        correct='루돌프',wrong1='사슴',wrong2='말',wrong3='강아지',
        romanization='rudolpeu',meaning='Tuần lộc',explanation='Rudolph cũ',
        wrong1_romanization='saseum',wrong1_meaning='Hươu',
        wrong2_romanization='mal',wrong2_meaning='Ngựa',
        wrong3_romanization='gangaji',wrong3_meaning='Chó con')


def corrected():return replace(original(),correct='순록',romanization='sullok')


def lookup(word,**kwargs):
    meanings={'순록':'tuần lộc','사슴':'hươu','말':'ngựa','강아지':'chó con'}
    gloss=meanings.get(word)
    return {'exact':bool(gloss),'entries':[] if gloss is None else [
        {'word':word,'glosses':[gloss],'meanings':[gloss],
         'target_code':'test-'+word,'link':'https://krdict.korean.go.kr/'}]}


def reviewer(q,provider):
    return {'available':True,'selected':q.correct,'back_translation':q.short_clue,
            'ambiguous':False,'conflicting_choices':[]}


class RepairEngineTests(unittest.TestCase):
    def call(self,**kwargs):
        dictionary=kwargs.pop('dictionary',Mock(lookup=Mock(side_effect=lookup)))
        with patch('studio.repair.get_keys',return_value=['synthetic-key']):
            return repair_quiz(original(),Mock(),dictionary=dictionary,
                               proposer=kwargs.pop('proposer',Mock(return_value=corrected())),
                               reviewer=kwargs.pop('reviewer',reviewer),**kwargs)

    def test_general_candidate_is_grounded_in_dictionary_then_reverified(self):
        candidate,report,errors=self.call()
        self.assertEqual(candidate.correct,'순록')
        self.assertEqual(candidate.romanization,'sullok')
        self.assertEqual(candidate.meaning,'tuần lộc')
        self.assertEqual(candidate.question,original().question)
        self.assertNotIn('Rudolph',candidate.explanation)
        self.assertEqual(report['outcome'],'verified')
        self.assertEqual(report['mode'],'strict')
        self.assertTrue(any(c['before']=='루돌프' and c['after']=='순록' for c in report['repair']['changes']))

    def test_full_proposal_prompt_preserves_question_not_wrong_label(self):
        payload={key:getattr(corrected(),key) for key in EDITABLE}
        with patch('studio.repair._model_review',return_value=payload) as model:
            candidate=propose(original(),{},'groq')
        self.assertEqual(candidate.correct,'순록')
        self.assertEqual(candidate.question,'Tuần lộc')
        self.assertIn('Giữ nguyên câu hỏi',model.call_args.args[1])

    def test_repair_of_other_words_is_not_a_rudolph_special_case(self):
        source=replace(original(),correct='학교',question='Nước',clue='Nước',meaning='Nước')
        proposed=replace(source,correct='물',romanization='mul')
        def words(word,**kwargs):
            return {'exact':True,'entries':[{'glosses':['nước'],'meanings':['nước']}]} if word=='물' else lookup(word)
        with patch('studio.repair.get_keys',return_value=['fake']):
            candidate,report,_=repair_quiz(source,Mock(),dictionary=Mock(lookup=Mock(side_effect=words)),
                proposer=Mock(return_value=proposed),reviewer=reviewer)
        self.assertEqual(candidate.correct,'물')
        self.assertEqual(report['outcome'],'verified')

    def test_missing_dictionary_key_never_calls_ai(self):
        proposer=Mock()
        candidate,_,errors=self.call(dictionary=Mock(lookup=Mock(return_value={
            'exact':False,'entries':[],'reason':'key_missing'})),proposer=proposer)
        self.assertIsNone(candidate);proposer.assert_not_called()
        self.assertIn('KRDict API key',errors[0])

    def test_no_ai_key_keeps_original(self):
        with patch('studio.repair.get_keys',return_value=[]):
            candidate,_,errors=repair_quiz(original(),Mock(),dictionary=Mock(lookup=Mock(side_effect=lookup)))
        self.assertIsNone(candidate);self.assertTrue(errors)

    def test_invented_word_cannot_be_saved(self):
        candidate,_,errors=self.call(proposer=Mock(return_value=replace(corrected(),correct='가짜단어')))
        self.assertIsNone(candidate);self.assertTrue(errors)

    def test_definition_mentions_do_not_ground_a_repair(self):
        def definitions(word,**kwargs):
            data=lookup(word)
            if word=='순록':data={'exact':True,'entries':[{'glosses':[], 'definitions':['tuần lộc']}]}
            return data
        candidate,_,_=self.call(dictionary=Mock(lookup=Mock(side_effect=definitions)))
        self.assertIsNone(candidate)

    def test_incorrect_meaning_for_distractor_is_not_overwritten_with_arbitrary_sense(self):
        candidate,_,_=self.call(proposer=Mock(return_value=replace(corrected(),wrong1_meaning='Máy bay')))
        self.assertIsNone(candidate)

    def test_reverse_ai_rejection_keeps_proposal_unsaved(self):
        candidate,_,errors=self.call(reviewer=lambda q,p:{'available':True,'selected':'말',
            'back_translation':'Ngựa','ambiguous':False,'conflicting_choices':[]})
        self.assertIsNone(candidate);self.assertTrue(errors)

    def test_cancel_and_deadline_propagate(self):
        for failure in (RunCancelled,BudgetExpired):
            with self.subTest(failure=failure),self.assertRaises(failure):
                self.call(proposer=Mock(side_effect=failure('stop')))


class RepairPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.service=Service(self.temp.name)
        self.qid=self.service.store.insert_question(original(),provider='gemini',batch_id='batch')

    def repair(self):
        with patch('studio.krdict.KRDict.lookup',side_effect=lookup),\
             patch('studio.repair.get_keys',return_value=['fake']),\
             patch('studio.repair.propose',return_value=corrected()),\
             patch('studio.review.blind_review',side_effect=reviewer):
            return self.service.repair_generated(self.qid)

    def test_service_saves_correct_word_hash_qa_and_history_together(self):
        result=self.repair()
        self.assertTrue(result['repaired'])
        self.assertEqual(self.service.store.get_questions([self.qid])[0].correct,'순록')
        row=self.service.store.list_questions()[0]
        self.assertEqual(row['status'],'auto_approved')
        self.assertEqual(row['batch_id'],'batch')
        self.assertEqual(row['qa_outcome'],'verified')
        history=self.service.store.repair_history(self.qid)
        self.assertEqual(history[0]['before']['correct'],'루돌프')
        self.assertEqual(history[0]['after']['correct'],'순록')
        with self.assertRaises(DuplicateQuestion):
            self.service.store.insert_question(replace(corrected(),question='Một câu hỏi khác'))

    def test_failed_qa_does_not_modify_original_or_create_revision(self):
        with patch('studio.krdict.KRDict.lookup',side_effect=lookup),\
             patch('studio.repair.get_keys',return_value=['fake']),\
             patch('studio.repair.propose',return_value=corrected()),\
             patch('studio.review.blind_review',return_value={'available':False}):
            result=self.service.repair_generated(self.qid)
        self.assertFalse(result['repaired'])
        self.assertEqual(self.service.store.get_questions([self.qid])[0],original())
        self.assertEqual(self.service.store.repair_history(self.qid),[])

    def verified(self):
        with patch('studio.repair.get_keys',return_value=['fake']):
            return repair_quiz(original(),Mock(),dictionary=Mock(lookup=Mock(side_effect=lookup)),
                               proposer=Mock(return_value=corrected()),reviewer=reviewer)[:2]

    def test_duplicate_replacement_rolls_back_all_changes(self):
        other=replace(corrected(),question='Con vật ở Bắc Cực',clue='Con vật ở Bắc Cực')
        self.service.store.insert_question(other)
        candidate,report=self.verified()
        with self.assertRaises(DuplicateQuestion):
            self.service.store.apply_repair(self.qid,original(),candidate,report)
        self.assertEqual(self.service.store.get_questions([self.qid])[0],original())
        self.assertEqual(self.service.store.repair_history(self.qid),[])

    def test_concurrent_edit_is_not_lost(self):
        candidate,report=self.verified()
        self.service.store.update_answer_details(self.qid,{'wrong1_meaning':'Nai'})
        with self.assertRaisesRegex(ValueError,'chỉnh sửa'):
            self.service.store.apply_repair(self.qid,original(),candidate,report)
        self.assertEqual(self.service.store.get_questions([self.qid])[0].wrong1_meaning,'Nai')
        self.assertEqual(self.service.store.repair_history(self.qid),[])

    def test_rendered_question_cannot_be_changed(self):
        candidate,report=self.verified()
        self.service.store.approve([self.qid]);self.service.store.mark_rendered([self.qid],'video.mp4')
        with self.assertRaises(ValueError):self.service.store.apply_repair(self.qid,original(),candidate,report)
        with self.assertRaises(ValueError):self.service.repair_generated(self.qid)

    def test_unverified_payload_is_not_saved(self):
        with self.assertRaises(ValueError):
            self.service.store.apply_repair(self.qid,original(),corrected(),{'outcome':'needs_review','mode':'strict'})

    def test_legacy_database_migrates_without_losing_questions(self):
        with self.service.store.db(immediate=True) as c:c.execute('DROP TABLE question_revisions')
        reopened=Store(self.service.store.path)
        self.assertEqual(reopened.get_questions([self.qid])[0],original())
        self.assertEqual(reopened.repair_history(self.qid),[])

    def test_http_repair_runs_in_background_and_exposes_history(self):
        server=create_server(self.service,port=0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_address[1])
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def get(path):
            with opener.open(base+path,timeout=5) as r:return json.load(r)
        try:
            with patch('studio.krdict.KRDict.lookup',side_effect=lookup),\
                 patch('studio.repair.get_keys',return_value=['fake']),\
                 patch('studio.repair.propose',return_value=corrected()),\
                 patch('studio.review.blind_review',side_effect=reviewer):
                request=urllib.request.Request(base+'/api/answers/repair',
                    data=json.dumps({'ids':[self.qid]}).encode(),headers={'Content-Type':'application/json'})
                with opener.open(request,timeout=5) as r:job=json.load(r)
                for _ in range(300):
                    status=get('/api/render/progress?job_id='+job['job_id'])
                    if status['status'] in ('completed','failed'):break
                    time.sleep(.01)
                self.assertEqual(status['status'],'completed',status)
                self.assertTrue(status['results'][0]['repaired'])
            self.assertEqual(get('/api/answers/history?id='+self.qid)['history'][0]['after']['correct'],'순록')
        finally:server.shutdown();server.server_close();thread.join(5)

    def test_autopilot_repairs_before_rendering_and_keeps_before_after(self):
        from studio.autopilot import Plan,run_pipeline
        # Existing draft is returned as the generated candidate for this test.
        updates=[]
        def render(ids,**kwargs):
            self.assertEqual(self.service.store.get_questions(ids)[0].correct,'순록')
            self.assertTrue(self.service.store.repair_history(ids[0]))
            return 'repaired-video.mp4'
        with patch.object(self.service,'generate',return_value={'accepted':[self.qid],'errors':[]}),\
             patch.object(self.service,'render_approved',side_effect=render) as renderer,\
             patch('studio.krdict.KRDict.lookup',side_effect=lookup),\
             patch('studio.repair.get_keys',return_value=['fake']),\
             patch('studio.repair.propose',return_value=corrected()),\
             patch('studio.review.blind_review',side_effect=reviewer):
            result=run_pipeline(self.service,Plan.parse({'videos':1,'per_video':1}),
                                lambda percent,phase:updates.append(phase))
        self.assertEqual(result['videos'],['repaired-video.mp4'])
        self.assertEqual(result['topics'][0]['repaired'][0]['id'],self.qid)
        self.assertTrue(any('Tự sửa' in phase for phase in updates))
        renderer.assert_called_once()

    def test_queue_is_exclusive_and_reports_each_item(self):
        queue=ExportQueue(self.service);entered=threading.Event();release=threading.Event()
        def work(*args,**kwargs):
            entered.set();release.wait(3)
            return {'id':self.qid,'status':'needs_review','repaired':False}
        with patch.object(self.service,'repair_generated',side_effect=work):
            result=queue.start_repair([self.qid]);self.assertTrue(entered.wait(2))
            try:
                with self.assertRaises(ValueError):queue.start_repair([self.qid])
            finally:release.set()
            for t in threading.enumerate():
                if t.name=='thse-repair-'+result['job_id'][:8]:t.join(5)
        self.assertEqual(queue.status(result['job_id'])['results'][0]['id'],self.qid)
        self.assertFalse(queue.is_running())

if __name__=='__main__':unittest.main()
