"""Synthetic regressions for classifier nouns and spaced Christmas phrases."""
from dataclasses import replace
import tempfile
import unittest
from unittest.mock import Mock,patch
from studio.ai import fallback,ProviderError
from studio.autopilot import Plan,run_pipeline
from studio.krdict import lookup_evidence,sense_overlap
from studio.quiz import Quiz
from studio.repair import repair_quiz
from studio.review import verify_quiz
from studio.run_limits import topic_budget
from studio.service import Service

GLOSSES={'크리스마스':'Giáng sinh','양말':'tất','모자':'mũ','사탕':'kẹo','장식':'đồ trang trí','종':'cái chuông'}
def lookup(word,**kwargs):
    gloss=GLOSSES.get(word)
    return {'exact':bool(gloss),'entries':[] if not gloss else [{'word':word,'pos':'명사',
        'glosses':[gloss],'meanings':[gloss],'target_code':'fixture-'+word}]}

def phrase_quiz():
    return Quiz(topic='Giáng sinh',question='Tất Giáng sinh',clue='Tất Giáng sinh',
        correct='크리스마스 양말',wrong1='크리스마스 모자',wrong2='크리스마스 사탕',wrong3='크리스마스 장식',
        romanization='keu',meaning='Tất Giáng sinh',wrong1_romanization='keu',wrong1_meaning='Mũ Giáng sinh',
        wrong2_romanization='keu',wrong2_meaning='Kẹo Giáng sinh',wrong3_romanization='keu',wrong3_meaning='Đồ trang trí Giáng sinh')

def reviewer(q,p):
    return {'available':True,'selected':q.correct,'back_translation':q.short_clue,
            'ambiguous':False,'conflicting_choices':[]}

class ClassifierTests(unittest.TestCase):
    def test_bell_with_classifier_is_still_bell(self):
        self.assertEqual(sense_overlap('Chuông',['cái chuông']),1)
        self.assertEqual(sense_overlap('Chiếc mũ',['mũ']),1)
        self.assertEqual(sense_overlap('cái',['cá']),0)
        self.assertLess(sense_overlap('tôi',['cái tôi']),.6)
        self.assertEqual(sense_overlap('cá',['cà']),0)
        self.assertEqual(sense_overlap('mùa đông',['mùa xuân']),.333)

class PhraseEvidenceTests(unittest.TestCase):
    def client(self):return Mock(lookup=Mock(side_effect=lookup))
    def test_bell_question_passes_with_classifier_in_dictionary_gloss(self):
        q=replace(phrase_quiz(),question='Chuông',clue='Chuông',correct='종',
                  meaning='Chuông',wrong1='모자',wrong2='사탕',wrong3='장식')
        result=verify_quiz(q,Mock(),dictionary=self.client(),reviewer=reviewer)
        self.assertEqual(result['outcome'],'verified',result['issues'])
        self.assertEqual(result['dictionary']['종']['translations'],['cái chuông'])
    def test_phrase_records_components_without_claiming_exact_headword(self):
        client=self.client();data=lookup_evidence(client,'크리스마스 양말')
        self.assertFalse(data['exact']);self.assertTrue(data['supported'])
        self.assertEqual([unit['word'] for unit in data['components']],['크리스마스','양말'])
        self.assertEqual(sense_overlap('Tất Giáng sinh',data['entries'][0]['glosses']),1)
        self.assertEqual(client.lookup.call_count,3)

    def test_full_phrase_requires_blind_review_and_is_labelled_as_component_evidence(self):
        judge=Mock(side_effect=reviewer)
        result=verify_quiz(phrase_quiz(),Mock(),dictionary=self.client(),reviewer=judge)
        self.assertEqual(result['outcome'],'verified',result['issues'])
        self.assertEqual(result['tier'],'dictionary_components+blind_ai')
        self.assertFalse(result['dictionary']['크리스마스 양말']['exact'])
        judge.assert_called_once()

    def test_missing_component_remains_unverified(self):
        q=replace(phrase_quiz(),correct='크리스마스 가짜단어')
        judge=Mock()
        result=verify_quiz(q,Mock(),dictionary=self.client(),reviewer=judge)
        self.assertEqual(result['outcome'],'needs_review');judge.assert_not_called()

    def test_all_components_cannot_override_ai_rejection(self):
        judge=Mock(return_value={'available':True,'selected':'크리스마스 모자',
                                 'back_translation':'Mũ Giáng sinh','ambiguous':False})
        result=verify_quiz(phrase_quiz(),Mock(),dictionary=self.client(),reviewer=judge)
        self.assertEqual(result['outcome'],'needs_review');judge.assert_called_once()

    def test_definition_only_is_not_component_evidence(self):
        def definitions(word,**kwargs):
            data=lookup(word)
            if word=='양말':data['entries'][0]['glosses']=[];data['entries'][0]['definitions']=['Tất Giáng sinh']
            return data
        data=lookup_evidence(Mock(lookup=Mock(side_effect=definitions)),'크리스마스 양말')
        self.assertFalse(data.get('supported',False))

    def test_verbal_components_are_not_treated_as_noun_compounds(self):
        def verbal(word,**kwargs):
            data=lookup(word)
            if word=='양말':data['entries'][0]['pos']='동사'
            return data
        data=lookup_evidence(Mock(lookup=Mock(side_effect=verbal)),'크리스마스 양말')
        self.assertFalse(data.get('supported',False))

    def test_true_synonymous_compound_distractor_still_blocks(self):
        def synonym(word,**kwargs):
            if word=='스타킹':return {'exact':True,'entries':[{'glosses':['tất'],'pos':'명사'}]}
            return lookup(word)
        q=replace(phrase_quiz(),wrong3='크리스마스 스타킹')
        result=verify_quiz(q,Mock(),dictionary=Mock(lookup=Mock(side_effect=synonym)),reviewer=Mock())
        self.assertEqual(result['outcome'],'needs_review')
        self.assertTrue(any('Đáp án nhiễu' in issue for issue in result['issues']))

    def test_repair_accepts_grounded_phrase_and_preserves_natural_vietnamese_order(self):
        source=replace(phrase_quiz(),correct='루돌프',romanization='rudolpeu')
        client=self.client()
        with patch('studio.repair.get_keys',return_value=['fake']):
            candidate,report,_=repair_quiz(source,Mock(),dictionary=client,
                proposer=Mock(return_value=phrase_quiz()),reviewer=reviewer)
        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.meaning,'Tất Giáng sinh')
        self.assertEqual(report['tier'],'dictionary_components+blind_ai')
        self.assertTrue(any(call.kwargs.get('fresh') for call in client.lookup.call_args_list))

    def test_phrase_search_is_bounded_and_does_not_guess_syllable_splits(self):
        client=self.client()
        data=lookup_evidence(client,'크리스마스양말')
        self.assertFalse(data.get('supported',False));self.assertEqual(client.lookup.call_count,1)
        lookup_evidence(client,'크리스마스 양말 모자 사탕')
        self.assertEqual(client.lookup.call_count,2)

class SupplementTests(unittest.TestCase):
    def test_third_generation_fills_short_batch_before_render(self):
        words=['물','차','학교','사람','바다']
        with tempfile.TemporaryDirectory() as td:
            service=Service(td);ids=[]
            for n,word in enumerate(words):
                q=replace(phrase_quiz(),question='Câu '+str(n),clue='Câu '+str(n),
                          correct=word,wrong1='봄',wrong2='여름',wrong3='가을')
                ids.append(service.store.insert_question(q,provider='gemini'))
            def verify(qid,mode='strict'):
                service.store.set_question_qa(qid,'verified',{'tier':'dictionary+blind_ai'})
                return {'id':qid,'status':'verified'}
            batches=[{'accepted':ids[:2]},{'accepted':ids[2:3]},{'accepted':ids[3:]}]
            with patch.object(service,'generate',side_effect=batches) as gen,\
                 patch.object(service,'verify_generated',side_effect=verify),\
                 patch.object(service,'render_approved',return_value='five-questions.mp4') as render:
                result=run_pipeline(service,Plan.parse({'videos':1,'per_video':5}),lambda *args:None)
            self.assertEqual(gen.call_count,3)
            self.assertEqual(result['videos'],['five-questions.mp4'])
            self.assertEqual(render.call_args.args[0],ids)

    def test_no_configured_ai_has_actionable_error(self):
        with patch('studio.keys.get_keys',return_value=[]),patch.dict('os.environ',{'ENABLE_OLLAMA':'0'}):
            with topic_budget(10),self.assertRaisesRegex(ProviderError,'API & Model'):
                fallback('Giáng sinh','TOPIK 1',3,[])

if __name__=='__main__':unittest.main()
