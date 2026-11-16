"""Synthetic QA evidence for tests of media/storage, never live certification."""
def report_for(quiz,**extra):
    return {'version':5,'mode':'strict','outcome':'verified','tier':'dictionary+blind_ai',
        'dictionary':{word:{'exact':True,'translations':[quiz.answer_details(word)['meaning'] or 'fixture'],
                            'headword_codes':['fixture-'+word]}
                      for word in (quiz.correct,quiz.wrong1,quiz.wrong2,quiz.wrong3)},
        'reviewer':{'available':True,'selected':quiz.correct,'ambiguous':False,
                    'conflicting_choices':[],'topic_fit':True},**extra}


def grant_qa(store,ids,**extra):
    for qid,quiz in zip(ids,store.get_questions(ids)):
        store.set_question_qa(qid,'verified',report_for(quiz,**extra),expected=quiz)
