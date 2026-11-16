"""Exact KRDict evidence, topic replacement and complete Gemini key failover."""
import io,json,os,tempfile,unittest,urllib.error
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock,patch
from studio.ai import post,ProviderError
from studio.keys import write_keys,next_key
from studio.krdict import lookup_headword
from studio.review import verify_quiz
from studio.quiz import Quiz
from studio.repair import replace_in_topic
from studio.service import Service
from studio.database import Store
from qa_fixtures import report_for,grant_qa


def quiz():
    return Quiz(topic='Đồ uống',question='Nước',clue='Nước',correct='물',wrong1='차',wrong2='우유',wrong3='맥주',
        meaning='Nước',romanization='mul',wrong1_meaning='Trà',wrong1_romanization='cha',
        wrong2_meaning='Sữa',wrong2_romanization='uyu',wrong3_meaning='Bia',wrong3_romanization='maekju')

WORDS={'물':'Nước','차':'Trà','우유':'Sữa','맥주':'Bia','주스':'Nước ép'}
def dictionary(word,**kw):
    return {'exact':word in WORDS,'entries':[] if word not in WORDS else
            [{'word':word,'target_code':'fixture-'+word,'glosses':[WORDS[word]]}]}

def judge(q,p):
    return {'available':True,'selected':q.correct,'ambiguous':False,'conflicting_choices':[],
            'back_translation':q.short_clue,'topic_fit':True}

class ExactDictionaryTests(unittest.TestCase):
    def test_whole_word_required_for_each_of_four_answers_including_assisted_legacy(self):
        q=quiz()
        for word in (q.correct,q.wrong1,q.wrong2,q.wrong3):
            for mode in ('strict','assisted'):
                def lookup(value,**kw):
                    return {'exact':False,'supported':True,'compound':True,'entries':[
                        {'glosses':[WORDS[value]],'target_code':'component-only'}]} if value==word else dictionary(value)
                reviewer=Mock(side_effect=judge)
                report=verify_quiz(q,Mock(),mode=mode,dictionary=Mock(lookup=Mock(side_effect=lookup)),reviewer=reviewer)
                self.assertEqual(report['outcome'],'needs_review');reviewer.assert_not_called()

    def test_exact_dictionary_without_official_entry_id_does_not_certify(self):
        def lookup(word):
            data=dictionary(word);data['entries'][0].pop('target_code');return data
        report=verify_quiz(quiz(),Mock(),dictionary=Mock(lookup=Mock(side_effect=lookup)),reviewer=Mock())
        self.assertEqual(report['outcome'],'needs_review')

    def test_all_four_headwords_and_reverse_review_are_recorded(self):
        report=verify_quiz(quiz(),Mock(),dictionary=Mock(lookup=Mock(side_effect=dictionary)),reviewer=judge)
        self.assertEqual(report['version'],5);self.assertEqual(report['outcome'],'verified')
        self.assertTrue(all(r['exact'] and any(r['headword_codes']) for r in report['dictionary'].values()))

    def test_manual_approval_and_video_export_cannot_bypass_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            service=Service(root);qid=service.store.insert_question(quiz())
            service.store.approve([qid])
            with self.assertRaises(ValueError):service.approve_questions([qid])
            with patch('studio.service.render') as encoder:
                with self.assertRaises(ValueError):service.render_approved([qid])
                encoder.assert_not_called()
            grant_qa(service.store,[qid]);service.approve_questions([qid])

    def test_migration_rechecks_manual_and_machine_approvals_preserves_rendered(self):
        with tempfile.TemporaryDirectory() as root:
            store=Store(Path(root)/'db.sqlite3');ids=[]
            for i,word in enumerate(['물','주스','커피']):
                ids.append(store.insert_question(replace(quiz(),correct=word,question='Câu '+str(i),clue='Câu '+str(i))))
            store.approve(ids);store.mark_rendered([ids[2]],Path(root)/'old.mp4')
            store.set_question_qa(ids[1],'verified',{'version':4})
            with store.db() as c:c.execute("DELETE FROM app_migrations WHERE name='krdict_exact_v5'")
            reopened=Store(store.path);states={q['id']:q['status'] for q in reopened.list_questions()}
            self.assertEqual([states[i] for i in ids],['draft','draft','rendered'])

class TopicReplacementTests(unittest.TestCase):
    def test_replacement_is_verified_same_topic_and_revision_preserves_old_question(self):
        source=replace(quiz(),question='Món đồ uống hiếm',clue='Món đồ uống hiếm',correct='가짜단어',meaning='Món đồ uống hiếm')
        with tempfile.TemporaryDirectory() as root:
            store=Store(Path(root)/'db.sqlite3');qid=store.insert_question(source)
            with patch('studio.repair.get_keys',return_value=['fake']):
                candidate,report,_=replace_in_topic(source,store,dictionary=Mock(lookup=Mock(side_effect=dictionary)),
                    proposer=Mock(return_value=quiz()),reviewer=judge)
            self.assertIsNotNone(candidate);self.assertTrue(report['replacement'])
            store.apply_topic_replacement(qid,source,candidate,report)
            self.assertEqual(store.get_questions([qid])[0],quiz())
            self.assertEqual(store.repair_history(qid)[0]['before']['question'],source.question)
            with store.db() as c:
                self.assertGreaterEqual(c.execute('SELECT COUNT(*) FROM question_memory WHERE source_id=?',(qid,)).fetchone()[0],4)

    def test_off_topic_or_unconfirmed_topic_replacements_are_never_saved(self):
        with patch('studio.repair.get_keys',side_effect=lambda p:['fake'] if p=='gemini' else []):
            for candidate,reviewer in [(replace(quiz(),topic='Động vật'),judge),
                (quiz(),lambda q,p:{**judge(q,p),'topic_fit':False})]:
                result,_,_=replace_in_topic(quiz(),Mock(),dictionary=Mock(lookup=Mock(side_effect=dictionary)),
                    proposer=Mock(return_value=candidate),reviewer=reviewer)
                self.assertIsNone(result)

    def test_missing_word_proposal_is_replaced_by_next_verified_candidate(self):
        unknown=replace(quiz(),correct='가짜단어')
        with patch('studio.repair.get_keys',side_effect=lambda p:['fake'] if p=='gemini' else []):
            proposer=Mock(side_effect=[unknown,quiz()])
            candidate,report,_=replace_in_topic(unknown,Mock(),dictionary=Mock(lookup=Mock(side_effect=dictionary)),
                proposer=proposer,reviewer=judge)
        self.assertEqual(candidate,quiz());self.assertEqual(proposer.call_count,2)

class GeminiKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.env=patch.dict(os.environ,{'THSE_KEYS_FILE':str(Path(self.tmp.name)/'keys.json'),
            'GEMINI_API_KEY':'','GEMINI_API_KEYS':''});self.env.start();self.addCleanup(self.env.stop)
        from studio import keys
        keys._BLOCKED_UNTIL.clear();keys._CURSOR.clear()
        write_keys('gemini',['key-one','key-two','key-three'])

    def response(self):return io.BytesIO(b'{"ok":true}')
    def error(self,code,headers=None):return urllib.error.HTTPError('https://example.invalid',code,'unavailable',headers or {},None)
    def request(self):return post('gemini','https://example.invalid',{}, {'x-goog-api-key':next_key('gemini')})

    def test_503_tries_remaining_keys_once_and_success_keeps_cursor(self):
        with patch('studio.ai.urllib.request.urlopen',side_effect=[self.error(503),self.error(503),self.response()]) as network:
            self.assertTrue(self.request()['ok'])
        self.assertEqual([c.args[0].get_header('X-goog-api-key') for c in network.call_args_list],['key-one','key-two','key-three'])
        self.assertEqual(next_key('gemini'),'key-one')

    def test_rotation_can_start_in_middle_of_pool_without_repeating_a_key(self):
        next_key('gemini')
        with patch('studio.ai.urllib.request.urlopen',side_effect=[self.error(503),self.error(503),self.response()]) as network:
            self.request()
        self.assertEqual([c.args[0].get_header('X-goog-api-key') for c in network.call_args_list],['key-two','key-three','key-one'])

    def test_progress_shows_attempt_numbers_without_exposing_keys(self):
        from studio.ai import ai_progress
        messages=[]
        with ai_progress(messages.append),patch('studio.ai.urllib.request.urlopen',side_effect=[self.error(503),self.response()]):
            self.request()
        self.assertTrue(any('key 2/3' in message for message in messages))
        self.assertFalse(any('key-one' in message or 'key-two' in message for message in messages))

    def test_all_keys_503_reports_counts_without_exposing_secrets(self):
        with patch('studio.ai.urllib.request.urlopen',side_effect=self.error(503)) as network:
            with self.assertRaises(ProviderError) as error:self.request()
        self.assertEqual(network.call_count,3);self.assertEqual(error.exception.keys_attempted,3)
        for key in ['key-one','key-two','key-three']:self.assertNotIn(key,str(error.exception))

    def test_429_stops_entire_provider_without_trying_other_keys(self):
        with patch('studio.ai.urllib.request.urlopen',side_effect=self.error(429)) as network:
            with self.assertRaises(ProviderError):self.request()
        self.assertEqual(network.call_count,1)
        with self.assertRaises(RuntimeError):next_key('gemini')

    def test_503_retry_after_is_honored_before_next_key(self):
        with patch('studio.ai.urllib.request.urlopen',side_effect=[self.error(503,{'Retry-After':'2'}),self.response()]), \
             patch('studio.ai._wait_before_retry') as wait:
            self.request();wait.assert_called_once_with(2)

    def test_retry_after_beyond_request_budget_does_not_hammer_remaining_keys(self):
        with patch('studio.ai.urllib.request.urlopen',side_effect=self.error(503,{'Retry-After':'3600'})) as network:
            with self.assertRaises(ProviderError) as error:self.request()
        self.assertEqual(network.call_count,1);self.assertEqual(error.exception.keys_total,3)
        self.assertEqual(next_key('gemini'),'key-two')

    def test_generation_review_repair_enrichment_and_seo_use_key_failover(self):
        from studio.ai import generate
        from studio.review import _model_review
        from studio.repair import propose,EDITABLE
        from studio.answer_enrichment import suggest,DETAIL_KEYS
        from studio.seo import draft
        q=quiz()
        cases=[('generate',lambda:generate('gemini',q.topic,q.level,1,[]),[q.to_dict()]),
            ('review',lambda:_model_review('gemini','prompt'),judge(q,'')),
            ('repair',lambda:propose(q,{},'gemini'),{key:getattr(q,key) for key in EDITABLE}),
            ('enrich',lambda:suggest(q,providers=('gemini',)),{key:getattr(q,key) for key in DETAIL_KEYS}),
            ('seo',lambda:draft([q],providers=('gemini',)),{'title':'Học từ vựng về đồ uống',
                'description':'Cùng học những từ tiếng Hàn về đồ uống qua câu hỏi trắc nghiệm thú vị dành cho người mới bắt đầu.',
                'hashtags':['HocTiengHan']})]
        for name,call,payload in cases:
            with self.subTest(workflow=name):
                write_keys('gemini',['key-one','key-two','key-three'])
                response=io.BytesIO(json.dumps({'candidates':[{'content':{'parts':[{'text':json.dumps(payload)}]}}]}).encode())
                with patch('studio.ai.urllib.request.urlopen',side_effect=[self.error(503),response]) as network:
                    call()
                self.assertEqual([c.args[0].get_header('X-goog-api-key') for c in network.call_args_list],['key-one','key-two'])

class HeadwordSenseTests(unittest.TestCase):
    def test_repair_selects_one_matching_translation_from_multi_gloss_headword(self):
        from studio.repair import repair_quiz
        source=replace(quiz(),correct='가짜단어')
        def lookup(word,**kwargs):
            data=dictionary(word)
            if word=='물':data['entries'][0]['glosses']=['nước; nước uống']
            return data
        with patch('studio.repair.get_keys',side_effect=lambda p:['fake'] if p=='gemini' else []):
            candidate,report,_=repair_quiz(source,Mock(),dictionary=Mock(lookup=Mock(side_effect=lookup)),
                proposer=Mock(return_value=quiz()),reviewer=judge)
        self.assertIsNotNone(candidate);self.assertEqual(candidate.meaning,'nước')
        self.assertEqual(report['outcome'],'verified')


class ReplacementIntegrationTests(unittest.TestCase):
    def test_repair_service_saves_certified_topic_replacement_and_history(self):
        from studio import review
        source=replace(quiz(),question='Đồ uống không có mục từ',clue='Đồ uống không có mục từ',
                       correct='가짜단어',meaning='Không có mục từ')
        client=Mock(lookup=Mock(side_effect=dictionary))
        def verify(candidate,store,provider,**kwargs):
            return review.verify_quiz(candidate,store,provider,dictionary=client,reviewer=judge)
        with tempfile.TemporaryDirectory() as root:
            service=Service(root);qid=service.store.insert_question(source,'gemini')
            with patch('studio.repair.repair_quiz',return_value=(None,None,['Không có mục từ'])), \
                 patch('studio.repair.get_keys',return_value=['fake']), \
                 patch('studio.repair.KRDict',return_value=client), \
                 patch('studio.repair._model_review',return_value=quiz().to_dict()), \
                 patch('studio.repair.verify_quiz',side_effect=verify):
                result=service.repair_generated(qid,{'status':'needs_review'})
            self.assertTrue(result['replaced']);self.assertEqual(service.require_dictionary_qa([qid]),[quiz()])
            self.assertEqual(service.store.repair_history(qid)[0]['before']['correct'],'가짜단어')

    def test_duplicate_topic_candidate_is_skipped_before_next_candidate(self):
        source=replace(quiz(),question='Chưa tìm được từ',clue='Chưa tìm được từ',correct='가짜단어')
        alternative=replace(quiz(),question='Cà phê',clue='Cà phê',correct='커피',meaning='Cà phê',romanization='keopi')
        def lookup(word,**kwargs):
            return {'exact':True,'entries':[{'word':'커피','target_code':'fixture-coffee','glosses':['Cà phê']}]} if word=='커피' else dictionary(word)
        with tempfile.TemporaryDirectory() as root:
            store=Store(Path(root)/'db.sqlite3');qid=store.insert_question(source);store.insert_question(quiz())
            with patch('studio.repair.get_keys',side_effect=lambda p:['fake'] if p=='gemini' else []):
                proposer=Mock(side_effect=[quiz(),alternative])
                candidate,report,_=replace_in_topic(source,store,source_id=qid,
                    dictionary=Mock(lookup=Mock(side_effect=lookup)),proposer=proposer,reviewer=judge)
            self.assertEqual(candidate,alternative);self.assertEqual(proposer.call_count,2)
