"""One publication policy: four exact KRDict headwords plus blind review."""
QA_VERSION=5


def certified(quiz,report):
    if (not isinstance(report,dict) or not isinstance(report.get('version'),int) or report['version']<QA_VERSION
        or report.get('outcome')!='verified' or report.get('mode')!='strict'):
        return False
    evidence=report.get('dictionary',{})
    if not isinstance(evidence,dict):return False
    for word in (quiz.correct,quiz.wrong1,quiz.wrong2,quiz.wrong3):
        entry=evidence.get(word,{})
        if not isinstance(entry,dict):return False
        translations=entry.get('translations');codes=entry.get('headword_codes')
        if not (entry.get('exact') is True and isinstance(translations,list) and
                any(isinstance(v,str) and v.strip() for v in translations) and isinstance(codes,list) and
                any(isinstance(v,str) and v.strip() for v in codes)):return False
    judge=report.get('reviewer',{})
    if not isinstance(judge,dict):return False
    return (judge.get('available') is True and judge.get('topic_fit') is True and judge.get('selected')==quiz.correct
            and judge.get('ambiguous') is False and not judge.get('conflicting_choices'))
