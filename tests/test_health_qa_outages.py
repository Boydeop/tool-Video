"""Regressions for the 1/3-video screenshot: health phrases and AI 503."""
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
import json
import unittest
import urllib.error
from unittest.mock import Mock,patch

from studio.ai import fallback,generate,generation_guidance,post,ProviderError,_retry_delay
from studio.krdict import lookup_evidence
from studio.quiz import Quiz
from studio.review import verify_quiz
from studio.run_limits import topic_budget


WORDS={
    '기침하다':('동사','ho'), '재채기하다':('동사','hắt hơi'),
    '배':('명사','bụng'), '머리':('명사','đầu'), '아프다':('형용사','đau'),
}


def dictionary(word,**kwargs):
    entry=WORDS.get(word)
    return {'exact':bool(entry),'entries':[] if not entry else [
        {'word':word,'pos':entry[0],'glosses':[entry[1]],
         'target_code':'test-'+word}]}


def health_quiz():
    return Quiz(topic='Cơ thể và sức khỏe',question='Ho',clue='Ho',correct='기침을 하다',
        wrong1='재채기를 하다',wrong2='배가 아프다',wrong3='머리가 아프다',
        meaning='Ho',romanization='gichimeul hada',
        wrong1_meaning='Hắt hơi',wrong1_romanization='jaechaegireul hada',
        wrong2_meaning='Đau bụng',wrong2_romanization='baega apeuda',
        wrong3_meaning='Đau đầu',wrong3_romanization='meoriga apeuda')


def reviewer(q,provider):
    return {'available':True,'selected':q.correct,'back_translation':q.short_clue,
            'ambiguous':False,'conflicting_choices':[]}


class HealthPhraseTests(unittest.TestCase):
    def test_object_hada_requires_the_complete_dictionary_compound(self):
        client=Mock(lookup=Mock(side_effect=dictionary))
        result=lookup_evidence(client,'기침을 하다')
        self.assertFalse(result['exact']);self.assertTrue(result['supported'])
        self.assertEqual(result['reason'],'dictionary_predicate_lemma')
        self.assertEqual(result['entries'][0]['word'],'기침하다')
        self.assertEqual(client.lookup.call_args_list[1].args[0],'기침하다')

    def test_subject_adjective_retains_both_component_codes(self):
        client=Mock(lookup=Mock(side_effect=dictionary))
        result=lookup_evidence(client,'배가 아프다')
        self.assertTrue(result['supported']);self.assertFalse(result['exact'])
        self.assertEqual([row['word'] for row in result['components']],['배','아프다'])
        self.assertEqual(result['entries'][0]['glosses'],['bụng đau'])
        self.assertTrue(all(row['headword_codes'] for row in result['components']))

    def test_screenshot_phrases_pass_only_with_dictionary_and_blind_review(self):
        q=health_quiz();client=Mock(lookup=Mock(side_effect=dictionary))
        report=verify_quiz(q,Mock(),dictionary=client,reviewer=reviewer)
        self.assertEqual(report['outcome'],'verified',report['issues'])
        self.assertEqual(report['tier'],'dictionary_components+blind_ai')
        unavailable=Mock(return_value={'available':False})
        result=verify_quiz(q,Mock(),dictionary=client,reviewer=unavailable)
        self.assertEqual(result['outcome'],'needs_review')
        unavailable.assert_called_once()

    def test_same_meaning_distractor_still_fails(self):
        q=replace(health_quiz(),wrong3='기침하다',wrong3_meaning='Ho')
        judge=Mock()
        report=verify_quiz(q,Mock(),dictionary=Mock(lookup=Mock(side_effect=dictionary)),reviewer=judge)
        self.assertEqual(report['outcome'],'needs_review')
        self.assertTrue(any('Đáp án nhiễu' in issue for issue in report['issues']))
        judge.assert_not_called()

    def test_bad_particles_missing_components_and_conjugations_are_not_guessed(self):
        client=Mock(lookup=Mock(side_effect=dictionary))
        for word in ('기침를 하다','배이 아프다','가짜가 아프다','배가 아파요','배가 하다'):
            with self.subTest(word=word):
                self.assertFalse(lookup_evidence(client,word).get('supported',False))

    def test_noun_plus_hada_without_compound_is_insufficient(self):
        def missing(word,**kwargs):
            if word=='기침':return {'exact':True,'entries':[{'word':word,'pos':'명사','glosses':['Ho']}]}
            if word=='하다':return {'exact':True,'entries':[{'word':word,'pos':'동사','glosses':['Làm']}]}
            return {'exact':False,'entries':[]}
        self.assertFalse(lookup_evidence(Mock(lookup=Mock(side_effect=missing)),'기침을 하다').get('supported',False))


class GenerationOutageTests(unittest.TestCase):
    def test_long_retry_after_is_not_shortened_and_invalid_numbers_are_rejected(self):
        self.assertEqual(_retry_delay({'Retry-After':'172800'},120),172800)
        for value in ('nan','inf','-1','not a date'):
            self.assertEqual(_retry_delay({'Retry-After':value},120),120)

    def test_strict_guidance_is_task_local_and_includes_previous_qa_feedback(self):
        q=health_quiz().to_dict()
        response={'candidates':[{'content':{'parts':[{'text':json.dumps([q])}]}}]}
        with patch('studio.ai.next_key',return_value='synthetic'),patch('studio.ai.get_model',return_value='test'),patch('studio.ai.post',return_value=response) as call:
            with generation_guidance('strict',['Không có mục từ cho đáp án nhiễu']):
                generate('gemini','Cơ thể','TOPIK 1',1,[])
            prompt=call.call_args.args[2]['contents'][0]['parts'][0]['text']
            self.assertIn('QA STRICT',prompt);self.assertIn('Không có mục từ',prompt)
            generate('gemini','Cơ thể','TOPIK 1',1,[])
            prompt=call.call_args.args[2]['contents'][0]['parts'][0]['text']
            self.assertNotIn('QA STRICT',prompt)

    def test_third_configured_provider_is_reached_under_topic_budget(self):
        with patch('studio.keys.get_keys',return_value=['synthetic']),patch('studio.ai.generate',side_effect=[
                ProviderError('HTTP 503',retry_after=30),ProviderError('HTTP 503',retry_after=30),[health_quiz()]]) as call:
            with topic_budget(210):provider,rows=fallback('Cơ thể','TOPIK 1',1,[])
        self.assertEqual(provider,'openrouter');self.assertEqual(len(rows),1)
        self.assertEqual([item.args[0] for item in call.call_args_list],['gemini','groq','openrouter'])

    def test_503_honors_retry_after_and_does_not_rotate_keys(self):
        error=urllib.error.HTTPError('https://example.invalid',503,'Unavailable',{'Retry-After':'90'},None)
        with patch('studio.ai.urllib.request.urlopen',side_effect=error) as request,patch('studio.ai.rate_limited') as cooldown:
            with self.assertRaises(ProviderError) as caught:post('gemini','https://example.invalid',{}, {})
        self.assertEqual(caught.exception.retry_after,90)
        self.assertTrue(caught.exception.retryable)
        self.assertIn('tạm thời',str(caught.exception))
        self.assertEqual(request.call_count,1);cooldown.assert_not_called()

    def test_429_honors_http_date_and_uses_provider_cooldown(self):
        when=format_datetime(datetime.now(timezone.utc)+timedelta(minutes=5),usegmt=True)
        error=urllib.error.HTTPError('https://example.invalid',429,'Limited',{'Retry-After':when},None)
        with patch('studio.ai.urllib.request.urlopen',side_effect=error),patch('studio.ai.rate_limited') as cooldown:
            with self.assertRaises(ProviderError) as caught:post('gemini','https://example.invalid',{}, {})
        self.assertGreater(caught.exception.retry_after,290)
        self.assertEqual(cooldown.call_args.args[1],caught.exception.retry_after)


if __name__=='__main__':unittest.main()
