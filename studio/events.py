"""Separate, predictable seasonal Korean-learning topics.

Fixed-date cultural dates only. Lunar dates such as Seollal and Chuseok need
a trusted year-specific calendar, so never guess those dates here.
"""
from __future__ import annotations
from datetime import date, datetime, timezone, timedelta

TZ_VIETNAM=timezone(timedelta(hours=7))
EVENTS=(
  {"key":"new-year","month":1,"day":1,"name":"Tết Dương lịch","korean":"새해",
   "topics":("Lời chúc năm mới tiếng Hàn","Kế hoạch năm mới bằng tiếng Hàn","Phong tục năm mới ở Hàn Quốc","Từ vựng đón năm mới")},
  {"key":"samiljeol","month":3,"day":1,"name":"Ngày Phong trào Độc lập Hàn Quốc","korean":"삼일절",
   "topics":("Lịch sử và từ vựng ngày 삼일절","Biểu tượng quốc gia Hàn Quốc")},
  {"key":"childrens-day","month":5,"day":5,"name":"Tết Thiếu nhi Hàn Quốc","korean":"어린이날",
   "topics":("Quà tặng ngày Thiếu nhi Hàn Quốc","Hoạt động gia đình ngày 어린이날")},
  {"key":"liberation-day","month":8,"day":15,"name":"Ngày Giải phóng Hàn Quốc","korean":"광복절",
   "topics":("Từ vựng ngày 광복절","Lịch sử hiện đại Hàn Quốc căn bản")},
  {"key":"hangul-day","month":10,"day":9,"name":"Ngày chữ Hangeul","korean":"한글날",
   "topics":("Ngày Hangeul và bảng chữ cái","Từ vựng về chữ viết Hàn Quốc","Luyện phát âm Hangul")},
  {"key":"pepero","month":11,"day":11,"name":"Ngày Pepero","korean":"빼빼로데이",
   "topics":("Quà tặng ngày Pepero","Lời chúc Pepero tiếng Hàn")},
  {"key":"christmas","month":12,"day":25,"name":"Giáng sinh ở Hàn Quốc","korean":"크리스마스",
   "topics":("Từ vựng Giáng sinh tiếng Hàn","Lời chúc Noel tiếng Hàn","Mua quà Giáng sinh ở Hàn Quốc","Món ăn Noel ở Hàn Quốc","Trang trí mùa Giáng sinh ở Hàn Quốc","Hội thoại dịp Giáng sinh ở Hàn Quốc")},
)
def today_vietnam()->date:
    return datetime.now(TZ_VIETNAM).date()

def upcoming_events(day:date|None=None,window_days=45):
    today=day or today_vietnam()
    if not isinstance(today,date) or not 1<=int(window_days)<=90:
        raise ValueError("Invalid event date/range")
    upcoming=[]
    for year in (today.year,today.year+1):
        for event in EVENTS:
            scheduled=date(year,event["month"],event["day"])
            offset=(scheduled-today).days
            if 0<=offset<=window_days:
                upcoming.append({
                    "key":event["key"],"name":event["name"],"korean":event["korean"],
                    "date":scheduled.isoformat(),"days_until":offset,
                    "topics":list(event["topics"])
                })
    return sorted(upcoming,key=lambda item:(item["days_until"],item["key"]))

def choose_production_topics(counts,number,mode="random",day=None):
    """Random remains independent. Mix adds at most 1 event slot."""
    from .autopilot import choose_distinct_topics
    if mode not in ("random","mix","events"):
        raise ValueError("Invalid event mode")
    random_topics=choose_distinct_topics(counts,number)
    if mode=="random":return random_topics,[]
    events=upcoming_events(day)
    if not events:
        return random_topics,[]
    event_topics=[]
    for event in events:
        for t in event["topics"]:
            if t not in event_topics:event_topics.append(t)
    if not event_topics:return random_topics,[]
    take=1 if mode=="mix" else min(number,len(event_topics))
    chosen=event_topics[:take]
    if mode=="events" and len(chosen)<number:
        # Fill remaining with random topics without duplicating an event.
        chosen.extend(t for t in random_topics if t not in chosen)
    else:
        chosen.extend(t for t in random_topics if t not in chosen)
    return chosen[:number],events
