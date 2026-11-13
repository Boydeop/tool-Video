"""Structural QA for Korean quiz generation. Not proof of translation accuracy."""
from __future__ import annotations
import re
from .quiz import Quiz

def inspect_quiz(q:Quiz)->list[str]:
    failures=[]
    try:q.validate()
    except ValueError as exc:return [str(exc)]
    clue=q.short_clue.strip()
    if len(clue)>65 or len(clue.split())>12:
        failures.append("Câu hỏi dài, khó xem trong Shorts")
    # Check raw 'meaning' rather than Quiz.vietnamese_meaning, which may
    # inherit the clue even if an AI did not supply an actual translation.
    if q.missing_answer_details or not q.meaning.strip():
        failures.append("Thiếu nghĩa hoặc phiên âm cho một trong bốn đáp án")
    choices=(q.correct,q.wrong1,q.wrong2,q.wrong3)
    if not all(re.search(r"[\uac00-\ud7a3]",value) for value in choices):
        failures.append("Có đáp án không chứa chữ Hàn")
    for value in choices:
        romanization=q.answer_details(value)["romanization"]
        meaning=q.answer_details(value)["meaning"]
        if romanization and not re.fullmatch(r"[A-Za-z\s\-'.’]+",romanization):
            failures.append("Phiên âm có ký tự không theo chuẩn Latin")
            break
        if len(meaning)>60:
            failures.append("Nghĩa một đáp án quá dài")
            break
    # A full grammar/context clue naturally differs from the literal meaning
    # of its correct choice. Comparing these strings as if they were identical
    # caused valid questions to be rejected in bulk.
    return failures
