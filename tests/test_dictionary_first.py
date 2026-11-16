"""Dictionary-first selection and the invalid reviewer response in the screenshot."""
import io,json,tempfile,unittest,unicodedata,urllib.parse
from pathlib import Path
from unittest.mock import Mock,patch
from studio.quiz import Quiz
from studio.review import blind_review,verify_quiz,_model_review
from studio.krdict import KRDict,parse_search
from studio.dictionary_bank import repair_from_bank
from studio.database import Store


def source():
    return Quiz(topic='Sở thích',question='Chụp ảnh',clue='Chụp ảnh',correct='사진찍기',
        wrong1='그림그리기',wrong2='요리하기',wrong3='노래하기',meaning='Chụp ảnh',romanization='sajin jjikgi',
        wrong1_meaning='Vẽ tranh',wrong1_romanization='geurim geurigi',
        wrong2_meaning='Nấu ăn',wrong2_romanization='yori hagi',wrong3_meaning='Ca hát',wrong3_romanization='norae hagi')

ENTRIES=[{'word':'요리','target_code':'101','glosses':['nấu ăn'],'pos':'명사'},
 {'word':'독서','target_code':'102','glosses':['đọc sách'],'pos':'명사'},
 {'word':'운동','target_code':'103','glosses':['thể thao'],'pos':'명사'},
 {'word':'여행','target_code':'104','glosses':['du lịch'],'pos':'명사'},
 {'word':'음악','target_code':'105','glosses':['âm nhạc'],'pos':'명사'}]

class Dictionary:
    def lookup(self,word,**kwargs):
        rows=[e for e in ENTRIES if e['word']==word]
        return {'exact':bool(rows),'entries':rows}
    def search(self,query):return {'entries':ENTRIES,'source':'KRDict'}

def judge(q,p):
    return {'available':True,'selected':q.correct,'topic_fit':True,'ambiguous':False,
            'conflicting_choices':[],'back_translation':q.short_clue}

def selection():
    return {'choices':[{'entry_id':str(101+i),'gloss_index':0,'romanization':roman}
                       for i,roman in enumerate(['yori','dokseo','undong','yeohaeng'])]}

class ReviewContractTests(unittest.TestCase):
    def run_review(self,call):
        with patch('studio.review.get_keys',side_effect=lambda p:['fake'] if p=='gemini' else []), \
             patch('studio.review.random.SystemRandom') as rng:
            rng.return_value.shuffle.side_effect=lambda choices:None
            return blind_review(source(),call=call)

    def test_letter_selection_and_explicit_string_booleans_are_normalized(self):
        report=self.run_review(Mock(return_value={'selected':'A','ambiguous':'false','topic_fit':'true',
             'back_translation':'Chụp ảnh','conflicting_choices':[]}))
        self.assertTrue(report['available']);self.assertEqual(report['selected'],source().correct)
        self.assertFalse(report['ambiguous'])

    def test_invalid_fields_get_one_guided_format_retry(self):
        call=Mock(side_effect=[{'selected':'unknown','ambiguous':'false'},
            {'selected':'A','ambiguous':False,'topic_fit':True,'back_translation':'Chụp ảnh'}])
        report=self.run_review(call)
        self.assertTrue(report['available']);self.assertEqual(call.call_count,2)
        self.assertIn('Phản hồi trước sai định dạng',call.call_args.args[1])

    def test_wrong_answer_and_conflict_ids_are_not_silently_accepted(self):
        report=self.run_review(Mock(return_value={'selected':'B','ambiguous':False,'topic_fit':True,
            'back_translation':'Vẽ tranh','conflicting_choices':['A','B']}))
        self.assertEqual(report['selected'],source().wrong1)
        self.assertEqual(report['conflicting_choices'],[source().correct,source().wrong1])

    def test_missing_boolean_and_unknown_conflicts_do_not_default_to_safe(self):
        for answer in [{'selected':'A','topic_fit':True,'back_translation':'Chụp ảnh'},
                       {'selected':'A','ambiguous':False,'topic_fit':True,'back_translation':'Chụp ảnh','conflicting_choices':['Z']}]:
            call=Mock(return_value=answer);report=self.run_review(call)
            self.assertFalse(report['available']);self.assertEqual(report['failure_kind'],'format')
            self.assertEqual(call.call_count,2);self.assertEqual(report['retry_after'],0)

    def test_hangul_unicode_normalization_preserves_choice_identity(self):
        report=self.run_review(Mock(return_value={'selected':unicodedata.normalize('NFD',source().correct),
            'ambiguous':False,'topic_fit':True,'back_translation':'Chụp ảnh'}))
        self.assertEqual(report['selected'],source().correct)

    def test_gemini_request_contains_structured_schema(self):
        with patch('studio.review.next_key',return_value='fake'),patch('studio.review.get_model',return_value='model'), \
             patch('studio.review.post',return_value={'candidates':[{'content':{'parts':[{'text':'{}'}]}}]}) as send:
            _model_review('gemini','prompt',response_schema={'type':'OBJECT'})
        self.assertEqual(send.call_args.args[2]['generationConfig']['responseSchema'],{'type':'OBJECT'})

class BankTests(unittest.TestCase):
    def model(self,provider,prompt,**kwargs):
        return {'queries':['취미','요리','운동']} if 'queries' in prompt else selection()

    def test_question_and_all_answers_are_taken_from_returned_entries(self):
        with tempfile.TemporaryDirectory() as root:
            store=Store(Path(root)/'db.sqlite3');qid=store.insert_question(source())
            with patch('studio.dictionary_bank.get_keys',side_effect=lambda p:['fake'] if p=='gemini' else []):
                q,report,_=repair_from_bank(source(),store,dictionary=Dictionary(),model=self.model,reviewer=judge,source_id=qid)
            self.assertIsNotNone(q);self.assertEqual(q.question,'Nấu ăn')
            self.assertEqual([q.correct,q.wrong1,q.wrong2,q.wrong3],[e['word'] for e in ENTRIES[:4]])
            self.assertTrue(report['dictionary_first']);self.assertEqual(report['dictionary_confirmed'],4)
            store.apply_topic_replacement(qid,source(),q,report)
            self.assertEqual(store.repair_history(qid)[0]['before']['correct'],'사진찍기')

    def test_ai_cannot_invent_an_entry_id_or_translation(self):
        def model(provider,prompt,**kwargs):
            if 'queries' in prompt:return {'queries':['취미']}
            value=selection();value['choices'][0]['entry_id']='invented';return value
        store=Mock();store.recent_prompts.return_value=[];store.remembered_dictionary_answers.return_value=set()
        with patch('studio.dictionary_bank.get_keys',side_effect=lambda p:['fake'] if p=='gemini' else []):
            q,_,issues=repair_from_bank(source(),store,dictionary=Dictionary(),model=model,reviewer=judge)
        self.assertIsNone(q);self.assertTrue(any('mã không có' in x for x in issues))

    def test_no_vi_gloss_or_official_id_is_never_a_bank_candidate(self):
        client=Dictionary();client.search=lambda query:{'entries':[{'word':'가짜단어','glosses':['bịa']}]}
        with patch('studio.dictionary_bank.get_keys',return_value=['fake']):
            q,_,_=repair_from_bank(source(),Mock(),dictionary=client,model=self.model,reviewer=judge)
        self.assertIsNone(q)

    def test_search_keeps_official_heads_without_claiming_query_is_exact(self):
        xml='<channel><total>2</total>'+''.join('<item><word>'+e['word']+'</word><target_code>'+e['target_code']+
            '</target_code><sense><translation><trans_lang>베트남어</trans_lang><trans_word>'+e['glosses'][0]+
            '</trans_word></translation></sense></item>' for e in ENTRIES[:2])+'</channel>'
        self.assertFalse(parse_search(xml.encode())['exact'])
        self.assertEqual(len(parse_search(xml.encode())['entries']),2)
        with tempfile.TemporaryDirectory() as root:
            store=Store(Path(root)/'db.sqlite3');requests=[]
            def transport(req):
                requests.append(urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query));return xml.encode()
            client=KRDict(store,transport=transport)
            with patch('studio.krdict.get_keys',return_value=['fake']):
                data=client.search('취미');exact=client.lookup('요리')
            self.assertEqual(len(data['entries']),2);self.assertTrue(exact['exact']);self.assertTrue(exact['cache_hit'])
            self.assertIsNone(store.krdict_cache_get('취미'))
            self.assertEqual(requests[0]['method'],['include'])
            with patch('studio.krdict.get_keys',return_value=['fake']):client.lookup('요리',fresh=True)
            self.assertEqual(requests[-1]['method'],['exact'])

class ServiceBankTests(unittest.TestCase):
    def test_service_reuses_official_pool_and_saves_dictionary_first_history(self):
        from studio.service import Service
        client=Dictionary();search=Mock(side_effect=client.search);calls=[]
        def model(provider,prompt,**kwargs):
            if 'queries' in prompt:return {'queries':['취미']}
            calls.append(prompt);answer=selection()
            if len(calls)>1:
                answer['choices'][0],answer['choices'][1]=answer['choices'][1],answer['choices'][0]
            return answer
        with tempfile.TemporaryDirectory() as root:
            service=Service(root)
            first=service.store.insert_question(source(),'gemini')
            second_source=Quiz(**{**source().to_dict(),'question':'Một câu khác','clue':'Một câu khác','correct':'가짜단어'})
            second=service.store.insert_question(second_source,'gemini')
            with patch('studio.krdict.KRDict.lookup',side_effect=client.lookup), \
                 patch('studio.krdict.KRDict.search',side_effect=search), \
                 patch('studio.dictionary_bank.get_keys',side_effect=lambda p:['fake'] if p=='gemini' else []), \
                 patch('studio.dictionary_bank._model_review',side_effect=model), \
                 patch('studio.review.blind_review',side_effect=judge):
                one=service.repair_generated(first);searched=search.call_count
                two=service.repair_generated(second)
            self.assertTrue(one['replaced']);self.assertTrue(two['replaced'])
            self.assertGreater(searched,0);self.assertEqual(search.call_count,searched)
            self.assertTrue(service.store.get_question_qa(first)['report']['dictionary_first'])
            self.assertEqual(service.store.repair_history(first)[0]['before']['correct'],source().correct)
            service.require_dictionary_qa([first,second])

    def test_four_found_headwords_with_reviewer_format_error_are_not_rewritten(self):
        from studio.service import Service
        with tempfile.TemporaryDirectory() as root:
            service=Service(root);qid=service.store.insert_question(source())
            initial={'status':'needs_review','dictionary_confirmed':4,'reviewer':{'available':False,'failure_kind':'format'}}
            with patch('studio.dictionary_bank.repair_from_bank') as bank:
                result=service.repair_generated(qid,initial);bank.assert_not_called()
            self.assertEqual(result['dictionary_confirmed'],4);self.assertFalse(result['repaired'])
            self.assertEqual(service.store.get_questions([qid])[0],source())

    def test_finished_job_separates_dictionary_count_from_full_qa_count(self):
        from studio.service import Service
        from studio.exports import ExportQueue
        import time
        with tempfile.TemporaryDirectory() as root:
            service=Service(root);qid=service.store.insert_question(source());queue=ExportQueue(service)
            with patch.object(service,'repair_generated',return_value={'id':qid,'status':'needs_review','dictionary_confirmed':4,'repaired':False}):
                job=queue.start_repair([qid])
                for _ in range(200):
                    result=queue.status(job['job_id'])
                    if result['status'] in ('completed','failed'):break
                    time.sleep(.01)
            self.assertEqual(result['status'],'completed');self.assertIn('KRDict đủ 4 mục từ: 1/1',result['phase'])
            self.assertIn('QA đạt 0/1',result['phase'])

class PoolRefreshTests(unittest.TestCase):
    def test_used_pool_refreshes_to_other_official_words_with_bounded_search(self):
        extra=[{'word':word,'target_code':str(201+i),'glosses':[gloss],'pos':'명사'}
            for i,(word,gloss) in enumerate([('춤','nhảy múa'),('노래','ca hát'),('그림','vẽ tranh'),('낚시','câu cá')])]
        class NextDictionary(Dictionary):
            def lookup(self,word,**kwargs):
                entries=[entry for entry in [*ENTRIES,*extra] if entry['word']==word]
                return {'exact':bool(entries),'entries':entries}
            def search(self,query):return {'entries':extra}
        def model(provider,prompt,**kwargs):
            if 'queries' in prompt:return {'queries':['취미']}
            return {'choices':[{'entry_id':e['target_code'],'gloss_index':0,'romanization':'test'} for e in extra]}
        with tempfile.TemporaryDirectory() as root:
            store=Store(Path(root)/'db.sqlite3');qid=store.insert_question(source())
            for entry in ENTRIES:
                q=Quiz(topic='Sở thích',question=entry['glosses'][0],correct=entry['word'],wrong1='봄',wrong2='여름',wrong3='가을')
                store.insert_question(q)
            state={'loaded':True,'entries':{e['target_code']:{'id':e['target_code'],'word':e['word'],'glosses':e['glosses']} for e in ENTRIES}}
            client=NextDictionary();search=Mock(side_effect=client.search);client.search=search
            with patch('studio.dictionary_bank.get_keys',side_effect=lambda p:['fake'] if p=='gemini' else []):
                q,report,_=repair_from_bank(source(),store,dictionary=client,model=model,reviewer=judge,source_id=qid,bank_state=state)
            self.assertEqual(q.correct,'춤');self.assertTrue(report['dictionary_first'])
            self.assertLessEqual(search.call_count,6)
