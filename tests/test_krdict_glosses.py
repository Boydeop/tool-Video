"""Regression for screenshot: winter rejected because other definitions mention it.

These are synthetic XML fixtures shaped like the API, not live API captures.
"""
from dataclasses import replace
import unittest
from unittest.mock import Mock
from studio.krdict import parse_search,sense_overlap
from studio.quiz import Quiz
from studio.review import verify_quiz


def seasons():
    return Quiz(topic='Bốn mùa',question='Mùa đông',clue='Mùa đông',
        correct='겨울',wrong1='봄',wrong2='여름',wrong3='가을',
        meaning='Mùa đông',romanization='gyeoul',
        wrong1_meaning='Mùa xuân',wrong1_romanization='bom',
        wrong2_meaning='Mùa hè',wrong2_romanization='yeoreum',
        wrong3_meaning='Mùa thu',wrong3_romanization='gaeul')


def entry(word,gloss,definition):
    xml=f'''<channel><total>1</total><item><word>{word}</word>
      <sense><translation><trans_lang>베트남어</trans_lang>
      <trans_word>{gloss}</trans_word><trans_dfn>{definition}</trans_dfn>
      </translation></sense></item></channel>'''.encode()
    return parse_search(xml,word)


def reviewer(q,provider):
    return {'available':True,'selected':q.correct,'back_translation':q.clue,
            'ambiguous':False,'conflicting_choices':[]}


class GlossEvidenceTests(unittest.TestCase):
    def dictionary(self):
        values={
          '겨울':entry('겨울','mùa đông','Mùa lạnh nhất trong năm.'),
          '봄':entry('봄','mùa xuân','Mùa giữa mùa đông và mùa hè.'),
          '여름':entry('여름','mùa hè','Mùa nóng nhất trong năm.'),
          '가을':entry('가을','mùa thu','Mùa giữa mùa hè và mùa đông.')}
        dictionary=Mock();dictionary.lookup.side_effect=lambda word:values[word]
        return dictionary

    def test_parser_preserves_gloss_and_definition_as_different_evidence(self):
        data=self.dictionary().lookup('봄')['entries'][0]
        self.assertEqual(data['glosses'],['mùa xuân'])
        self.assertEqual(data['definitions'],['Mùa giữa mùa đông và mùa hè.'])
        self.assertIn('Mùa giữa mùa đông và mùa hè.',data['meanings'])

    def test_winter_is_not_ambiguous_just_because_other_definitions_mention_it(self):
        result=verify_quiz(seasons(),Mock(),dictionary=self.dictionary(),reviewer=reviewer)
        self.assertEqual(result['outcome'],'verified',result['issues'])
        self.assertEqual(result['dictionary']['봄']['translations'],['mùa xuân'])
        self.assertLess(result['dictionary']['봄']['match_to_clue'],.7)
        self.assertLess(result['dictionary']['가을']['match_to_clue'],.7)

    def test_real_synonymous_distractor_still_blocks_approval(self):
        dictionary=self.dictionary()
        lookup=dictionary.lookup.side_effect
        dictionary.lookup.side_effect=lambda word:entry(word,'mùa đông','') if word=='봄' else lookup(word)
        judge=Mock()
        result=verify_quiz(seasons(),Mock(),dictionary=dictionary,reviewer=judge)
        self.assertEqual(result['outcome'],'needs_review')
        self.assertTrue(any('Đáp án nhiễu' in issue for issue in result['issues']))
        judge.assert_not_called()

    def test_definition_alone_cannot_certify_correct_answer(self):
        dictionary=self.dictionary();lookup=dictionary.lookup.side_effect
        dictionary.lookup.side_effect=lambda word:entry(word,'','Mùa đông') if word=='겨울' else lookup(word)
        result=verify_quiz(seasons(),Mock(),dictionary=dictionary,reviewer=Mock())
        self.assertEqual(result['outcome'],'needs_review')

    def test_missing_proper_name_stays_reviewable_in_strict_mode(self):
        q=replace(seasons(),question='Tuần lộc',clue='Tuần lộc',meaning='Tuần lộc',
                  correct='루돌프',romanization='rudolpeu')
        dictionary=Mock();dictionary.lookup.return_value={'exact':False,'entries':[]}
        judge=Mock()
        result=verify_quiz(q,Mock(),dictionary=dictionary,reviewer=judge)
        self.assertEqual(result['outcome'],'needs_review')
        judge.assert_not_called()

    def test_explanatory_fragments_are_not_synonyms(self):
        self.assertLess(sense_overlap('Mùa đông',['Mùa giữa mùa đông và mùa hè']),.6)
        self.assertEqual(sense_overlap('Mùa đông',['Mùa xuân (sau mùa đông)']),.333)
        self.assertEqual(sense_overlap('Mùa đông',['mùa đông; mùa lạnh']),1)
