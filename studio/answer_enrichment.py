"""AI-assisted (unverified) details for the four Korean choices of a quiz.

A content reviewer should check all meanings and Revised Romanization.
Credentials remain in user's local key store and never go to the browser.
"""
import json
from dataclasses import replace
from .ai import post, ProviderError
from .keys import next_key
from .models import get_model
from .quiz import Quiz

DETAIL_KEYS=("romanization","meaning","wrong1_romanization","wrong1_meaning",
             "wrong2_romanization","wrong2_meaning","wrong3_romanization","wrong3_meaning")

def suggest(quiz:Quiz,providers=("gemini","groq","openrouter")):
    prompt=(
        "Bạn là chuyên gia từ điển Hàn-Việt. Cho bốn lựa chọn tiếng Hàn trong câu trắc nghiệm, "
        "hãy trả JSON object CHỈ gồm 8 trường "
        +", ".join(DETAIL_KEYS)+". Mỗi đáp án có phiên âm Latin đúng chuẩn "
        "Revised Romanization và nghĩa Việt ngắn chính xác, KHÁC NHAU cho từng từ, "
        "không lấy nghĩa đáp án đúng áp cho đáp án sai. "
        "Nếu chưa rõ nghĩa hoặc không chắc về chuyển âm, để chuỗi rỗng. "
        "Tất cả phải dựa vào nguyên gốc tiếng Hàn. Dữ liệu: "
        +json.dumps({"topic":quiz.topic,"question":quiz.short_clue,
                      "correct":quiz.correct,"wrong1":quiz.wrong1,
                      "wrong2":quiz.wrong2,"wrong3":quiz.wrong3},ensure_ascii=False))
    errors=[]
    for provider in providers:
        try:
            key=next_key(provider)
            model=get_model(provider)
            if provider=="gemini":
                result=post(provider,
                    "https://generativelanguage.googleapis.com/v1beta/models/"+model+":generateContent",
                    {"contents":[{"parts":[{"text":prompt}]}],
                     "generationConfig":{"responseMimeType":"application/json"}},
                    {"x-goog-api-key":key})
                response="".join(t.get("text","") for t in result["candidates"][0]["content"]["parts"])
            else:
                endpoint=("https://api.groq.com/openai/v1/chat/completions"
                         if provider=="groq" else "https://openrouter.ai/api/v1/chat/completions")
                result=post(provider,endpoint,{"model":model,"temperature":0.25,
                    "messages":[{"role":"user","content":prompt}]},
                    {"Authorization":"Bearer "+key})
                response=result["choices"][0]["message"]["content"]
            data=json.loads(response)
            if not isinstance(data,dict):raise ValueError("AI details must be a JSON object")
            fields={}
            for key in DETAIL_KEYS:
                value=data.get(key,"")
                if not isinstance(value,str):raise ValueError("Metadata must be strings")
                value=value.strip()
                if len(value)>100:raise ValueError("Answer details too long")
                # Preserve explicitly curated values already stored in the question.
                fields[key]=getattr(quiz,key) or value
            updated=replace(quiz,**fields)
            updated.validate()
            return updated,provider
        except (ProviderError,ValueError,TypeError,KeyError,IndexError,RuntimeError) as exc:
            errors.append(provider+": "+str(exc)[:90])
    raise ProviderError("Không bổ sung được nghĩa/phiên âm: "+" | ".join(errors))
