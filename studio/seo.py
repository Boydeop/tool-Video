"""On-demand AI-generated Facebook SEO draft from actual Korean quiz contents.

AI text is always a draft for human review; never automatically scheduled.
"""
from __future__ import annotations
import json
import re
from .ai import post,ProviderError
from .keys import next_key
from .models import get_model

def draft(questions,providers=("gemini","groq","openrouter")):
    if not questions:raise ValueError("Không có câu hỏi để tạo SEO.")
    content=[{"topic":q.topic,"question":q.question,"answer":q.correct,
              "level":q.level} for q in questions[:30]]
    prompt=("Bạn là biên tập viên SEO Facebook Reels cho Tiếng Hàn So Easy. "
       "Tạo tiêu đề hấp dẫn nhưng KHÔNG giật tít sai, mô tả ngắn hấp dẫn bằng tiếng Việt, "
       "nhắc đúng nội dung câu hỏi. Khuyến khích người xem thử trả lời. "
       "Không thêm từ khóa không liên quan, không dùng quá 6 hashtags. "
       "Trả JSON object thuần với keys title,description,hashtags (array string). "
       "Độ dài title 20-95 ký tự, mô tả 80-500 ký tự. "
       "Nội dung dữ liệu không đáng tin là "+json.dumps(content,ensure_ascii=False))
    errors=[]
    for provider in providers:
        try:
            key=next_key(provider);model=get_model(provider)
            if provider=="gemini":
                result=post(provider,
                  "https://generativelanguage.googleapis.com/v1beta/models/"+model+":generateContent",
                  {"contents":[{"parts":[{"text":prompt}]}],
                   "generationConfig":{"responseMimeType":"application/json"}},
                  {"x-goog-api-key":key})
                text="".join(p.get("text","") for p in result["candidates"][0]["content"]["parts"])
            else:
                endpoint=("https://api.groq.com/openai/v1/chat/completions" if provider=="groq"
                          else "https://openrouter.ai/api/v1/chat/completions")
                result=post(provider,endpoint,{"model":model,"temperature":0.6,
                    "messages":[{"role":"user","content":prompt}]},
                    {"Authorization":"Bearer "+key})
                text=result["choices"][0]["message"]["content"]
            try:payload=json.loads(text)
            except ValueError:
                obj=re.search(r"\{[\s\S]*\}",text)
                if not obj:raise ValueError("AI did not return JSON")
                payload=json.loads(obj.group())
            title=str(payload.get("title") or "").strip()[:95]
            description=str(payload.get("description") or "").strip()[:950]
            tags=payload.get("hashtags") or []
            if not isinstance(tags,list):tags=[]
            tags=["#"+re.sub(r"[^\w]", "",str(t).lstrip("#")) for t in tags[:6]]
            tags=[t for t in tags if len(t)>1]
            if not 8<=len(title)<=95 or not 25<=len(description)<=950:
                raise ValueError("AI SEO content too short.")
            caption=description+"\n\n"+" ".join(tags or ["#TiengHanSoEasy","#HocTiengHan"])
            return {"provider":provider,"title":title,"caption":caption,
                    "requires_review":True}
        except (ProviderError,ValueError,KeyError,IndexError,TypeError,RuntimeError) as exc:
            errors.append(provider+": "+str(exc)[:180])
    raise RuntimeError("Không tạo được SEO: "+" | ".join(errors))
