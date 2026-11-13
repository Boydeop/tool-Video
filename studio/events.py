"""Date-aware Korean learning events, separate from the normal topic rotation.

Gregorian dates only. Lunar holidays (Seollal, Chuseok) are deliberately NOT
calculated from a fixed solar date; those need a separately maintained,
authoritative yearly calendar.
"""
from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
from typing import Optional

VIETNAM_TZ=timezone(timedelta(hours=7))
EVENTS={
 "christmas":{"name":"Giáng sinh / Noel","month":12,"day":25,"culture":"Hàn Quốc",
    "topics":["Noel: lời chúc tiếng Hàn","Giáng sinh: đồ trang trí","Noel: món quà và tặng quà",
              "Mùa đông Hàn Quốc dịp Giáng sinh","Noel: thời tiết và tuyết",
              "Đêm Giáng sinh: gia đình và bạn bè","Noel: hoạt động cuối năm",
              "Giáng sinh: mua sắm và cửa hàng","Noel: đồ ăn và tiệc tối",
              "Noel: lịch hẹn và lời mời","Noel: câu nói thường gặp",
              "Noel: cảm xúc và kỷ niệm"]},
 "new_year":{"name":"Năm mới dương lịch","month":1,"day":1,"culture":"Việt Nam · Hàn Quốc",
    "topics":["Lời chúc năm mới tiếng Hàn","Kế hoạch đầu năm","Năm mới và gia đình",
              "Lịch, ngày và tháng đầu năm","Mục tiêu học tiếng Hàn năm mới",
              "Hẹn gặp bạn bè đầu năm","Năm mới: thói quen tốt","Năm mới: cảm xúc",
              "Năm mới: du lịch","Năm mới: công việc","Năm mới: sức khỏe","Năm mới: món ăn"]},
 "valentine":{"name":"Valentine","month":2,"day":14,"culture":"Hàn Quốc",
    "topics":["Valentine: tình cảm","Valentine: quà và hoa","Valentine: lời tỏ tình",
              "Valentine: món ăn","Valentine: hẹn hò","Valentine: cảm xúc",
              "Valentine: từ vựng về chocolate","Valentine: lời chúc",
              "Valentine: mời đi chơi","Valentine: tình bạn",
              "Valentine: kế hoạch cuối tuần","Valentine: tin nhắn"]},
 "children_day":{"name":"Ngày Thiếu nhi Hàn Quốc","month":5,"day":5,"culture":"Hàn Quốc",
    "topics":["어린이날: trẻ em và gia đình","어린이날: lời chúc","어린이날: trò chơi",
              "어린이날: món quà","어린이날: trường học","어린이날: sở thích",
              "어린이날: công viên","어린이날: hoạt động gia đình"]},
 "hangul_day":{"name":"Ngày chữ Hàn 한글날","month":10,"day":9,"culture":"Hàn Quốc",
    "topics":["한글날: bảng chữ cái Hangeul","한글날: nguyên âm",
              "한글날: phụ âm","한글날: ghép âm tiết","한글날: cách đọc",
              "한글날: từ vựng cơ bản","한글날: chào hỏi","한글날: văn hóa ngôn ngữ"]},
 "pepero":{"name":"Ngày Pepero","month":11,"day":11,"culture":"Hàn Quốc",
    "topics":["빼빼로데이: đồ ngọt","빼빼로데이: quà tặng",
              "빼빼로데이: bạn bè","빼빼로데이: mua sắm",
              "빼빼로데이: lời nhắn","빼빼로데이: kỷ niệm",
              "빼빼로데이: cảm xúc","빼빼로데이: ăn uống"]},
}

def today_vietnam():
    return datetime.now(VIETNAM_TZ).date()

def event_on_or_after(event_id,day:date):
    e=EVENTS[event_id]
    candidate=date(day.year,e["month"],e["day"])
    if candidate<day:candidate=date(day.year+1,e["month"],e["day"])
    return candidate

def upcoming_events(day:date|None=None,window_days=55):
    """Future events only. Christmas is available from 1 Nov (55-day window)."""
    day=day or today_vietnam()
    if not isinstance(day,date) or not 1<=window_days<=120:
        raise ValueError("Invalid date/event window")
    rows=[]
    for event_id,meta in EVENTS.items():
        future=event_on_or_after(event_id,day)
        days=(future-day).days
        if days<=window_days:
            rows.append({"id":event_id,"name":meta["name"],
                "date":future.isoformat(),"days_until":days,
                "culture":meta["culture"],
                "topics":len(meta["topics"])})
    rows.sort(key=lambda r:(r["days_until"],r["id"]))
    return rows

def resolve_event(event_id:str="",day:date|None=None,window_days=55):
    available=upcoming_events(day,window_days)
    if not available:raise ValueError("Chưa có sự kiện sắp tới trong 55 ngày.")
    if event_id:
        for item in available:
            if item["id"]==event_id:return item
        raise ValueError("Sự kiện đã chọn chưa nằm trong khoảng thời gian sắp tới.")
    return available[0]

def event_topics(event_id:str,number:int):
    if event_id not in EVENTS:raise ValueError("Unknown event")
    topics=EVENTS[event_id]["topics"]
    if not 1<=number<=len(topics):
        raise ValueError("Sự kiện không đủ chủ đề khác nhau cho lượt này.")
    return topics[:number]
