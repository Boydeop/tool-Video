"""Reproduce v1.5.0 hobby QA and reviewer outage screenshots offline."""
from dataclasses import replace
from unittest.mock import Mock,patch
import tempfile
import unittest
from studio.quiz import Quiz
from studio.krdict import lookup_evidence,sense_overlap
from studio.review import verify_quiz,blind_review
from studio.repair import repair_quiz
from studio.ai import ProviderError
from studio.service import Service
from studio.run_limits import topic_budget

WORDS={'사진':('명사','ảnh'),'찍다':('동사','chụp'),'그림':('명사','tranh'),
       '그리다':('동사','vẽ'),'요리하다':('동사','nấu ăn'),'노래하다':('동사','ca hát'),
       '음악':('명사','âm nhạc'),'감상':('명사','sự thưởng thức'),
       '영화':('명사','phim'),'독서':('명사','đọc sách'),'운동':('명사','thể thao'),
       '여행':('명사','du lịch')}

def dictionary(word,**kwargs):
    found=WORDS.get(word)
    return {'exact':bool(found),'entries':[] if not found else
            [{'word':word,'pos':found[0],'glosses':[found[1]],'target_code':'test-'+word}]}

def photos():
    return Quiz(topic='Sở thích',question='Chụp ảnh',clue='Chụp ảnh',correct='사진찍기',
        wrong1='그림그리기',wrong2='요리하기',wrong3='노래하기',meaning='Chụp ảnh',romanization='sajin jjikgi',
        wrong1_meaning='Vẽ tranh',wrong1_romanization='geurim geurigi',
        wrong2_meaning='Nấu ăn',wrong2_romanization='yori hagi',
        wrong3_meaning='Ca hát',wrong3_romanization='norae hagi')

def reviewer(q,p):
    return {'topic_fit':True,'available':True,'selected':q.correct,'back_translation':q.short_clue,
            'ambiguous':False,'conflicting_choices':[]}

class HobbyEvidenceTests(unittest.TestCase):
    def client(self):return Mock(lookup=Mock(side_effect=dictionary))
    def test_compact_photo_label_has_confirmed_noun_and_verb(self):
        result=lookup_evidence(self.client(),'사진찍기')
        self.assertTrue(result['supported']);self.assertFalse(result['exact'])
        self.assertEqual([p['word'] for p in result['components']],['사진','찍다'])
        self.assertEqual(sense_overlap('Chụp ảnh',result['entries'][0]['glosses']),1)

    def test_photo_components_do_not_replace_whole_dictionary_entries(self):
        result=verify_quiz(photos(),Mock(),dictionary=self.client(),reviewer=reviewer)
        self.assertEqual(result['outcome'],'needs_review',result['issues'])
        self.assertEqual(result['tier'],'needs_review')
        self.assertEqual(verify_quiz(photos(),Mock(),dictionary=self.client(),
            reviewer=Mock(return_value={'available':False}))['outcome'],'needs_review')

    def test_music_and_movie_labels_need_their_own_headwords(self):
        q=replace(photos(),question='Nghe nhạc',clue='Nghe nhạc',correct='음악감상',
            wrong1='영화감상',wrong2='독서',wrong3='운동',meaning='Thưởng thức âm nhạc',
            wrong1_meaning='Xem phim',wrong2_meaning='Đọc sách',wrong3_meaning='Thể thao')
        result=verify_quiz(q,Mock(),dictionary=self.client(),reviewer=reviewer)
        self.assertEqual(result['outcome'],'needs_review',result['issues'])

    def test_screenshot_bad_proposals_remain_rejected(self):
        for clue,gloss in [('Du lịch','việc đi dạo, việc đi tản bộ'),
                           ('Thể thao','sự bơi lội'),('Đọc sách','âm nhạc'),('Chụp ảnh','ảnh')]:
            self.assertLess(sense_overlap(clue,[gloss]),.6,(clue,gloss))
        q=replace(photos(),wrong1='사진',wrong1_meaning='Ảnh')
        # An image is not the activity of taking a photo; no fabricated verb.
        self.assertEqual(sense_overlap('Chụp ảnh',['ảnh']),.5)
        self.assertFalse(lookup_evidence(self.client(),'가짜단어찍기').get('supported',False))

    def test_compact_analysis_is_bounded_and_does_not_repeat_lookups(self):
        client=Mock(lookup=Mock(return_value={'exact':False,'entries':[]}))
        self.assertFalse(lookup_evidence(client,'가나다라마바사아자차').get('supported',False))
        words=[call.args[0] for call in client.lookup.call_args_list]
        self.assertEqual(len(words),len(set(words)));self.assertLessEqual(len(words),10)

    def test_close_wrong_display_meaning_is_fixed_without_changing_korean(self):
        q=replace(photos(),correct='운동',question='Thể thao',clue='Thể thao',meaning='Thể thao',
            wrong1='독서',wrong1_meaning='Đọc sách',wrong2='음악',wrong2_meaning='Âm nhạc',
            wrong3='사진',wrong3_meaning='Chụp ảnh')
        candidate,report,_=repair_quiz(q,Mock(),dictionary=self.client(),reviewer=reviewer)
        self.assertIsNotNone(candidate);self.assertEqual(candidate.wrong3_meaning,'ảnh')
        for field in ['correct','wrong1','wrong2','wrong3']:self.assertEqual(getattr(q,field),getattr(candidate,field))
        self.assertEqual(report['outcome'],'verified')

class ReviewerOutageTests(unittest.TestCase):
    def test_budgeted_reviewer_can_reach_third_provider(self):
        q=photos();calls=[]
        def call(provider,prompt):
            calls.append(provider)
            if len(calls)<3:raise ProviderError('temporary outage',retry_after=30)
            return {'selected':q.correct,'back_translation':q.short_clue,'ambiguous':False,'topic_fit':True}
        with patch('studio.review.get_keys',return_value=['fake']),topic_budget():
            result=blind_review(q,call=call)
        self.assertTrue(result['available']);self.assertEqual(len(calls),3)

    def test_malformed_review_response_provides_retry_guidance(self):
        with patch('studio.review.get_keys',return_value=['fake']):
            result=blind_review(photos(),call=Mock(side_effect=ProviderError('Invalid response')))
        self.assertFalse(result['available']);self.assertGreaterEqual(result['retry_after'],15)
        self.assertIn('Invalid response',result['errors'][0])

    def test_reviewer_outage_never_triggers_answer_rewrite(self):
        with tempfile.TemporaryDirectory() as root:
            service=Service(root);qid=service.store.insert_question(photos(),'gemini')
            initial={'status':'needs_review','retry_after':30,'blocked':False,
                     'issues':['AI reviewer HTTP 503']}
            with patch('studio.repair.repair_quiz') as repair:
                result=service.repair_generated(qid,initial)
                repair.assert_not_called()
            self.assertFalse(result['repaired']);self.assertEqual(service.store.get_questions([qid])[0],photos())
