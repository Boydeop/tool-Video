"""Conservative structural preflight for unreviewed AI Korean content.

No algorithmic gate can prove semantic accuracy; AUTO status is distinctly
recorded as machine-checked, not human approved.
"""
from __future__ import annotations
import re
from .quiz import Quiz,normalize

def inspect_quiz(q:Quiz)->list[str]:
    failures=[]
    try:q.validate()
    except ValueError as exc:return [str(exc)]
    clue=q.short_clue.strip()
    if len(clue)>45 or len(clue.split())>9:failures.append("Câu hỏi quá dài")
    if q.missing_answer_details or not q.meaning.strip():
        failures.append("Thiếu nghĩa hoặc phiên âm được cung cấp rõ ràng cho 4 đáp án")
    if any(not re.fullmatch(r"[A-Za-z\s\-'.]+",q.answer_details(a)["romanization"])
           for a in (q.correct,q.wrong1,q.wrong2,q.wrong3)
           if q.answer_details(a)["romanization"]):
        failures.append("Phiên âm có ký tự không phù hợp")
    if any(len(q.answer_details(a)["meaning"])>45 for a in
            (q.correct,q.wrong1,q.wrong2,q.wrong3)):
        failures.append("Nghĩa đáp án dài")
    # A contextual clue may legitimately differ from the dictionary meaning:
    # e.g. "Giá rẻ nhưng" -> 하지만 ("nhưng"). Never require literal equality.
    # Keep structure checks strict; semantic accuracy still needs human QA.
    return failures
