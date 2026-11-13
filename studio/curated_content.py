"""Small, reviewed-by-construction Korean vocabulary seed for offline fallback.

The primary production source remains AI. These simple, individually defined
word/meaning/romanization triples are only a bounded safety net when AI omits
fields or providers are temporarily unavailable. Do not label as teacher-reviewed.
"""
from __future__ import annotations
from .quiz import Quiz
from .database import DuplicateQuestion

# (Hangul, Revised Romanization, Vietnamese meaning)
BANKS={
"Gia đình":[("엄마","eomma","Mẹ"),("아빠","appa","Bố"),
 ("어머니","eomeoni","Mẹ (kính trọng)"),("아버지","abeoji","Bố (kính trọng)"),
 ("누나","nuna","Chị gái (nam dùng)"),("언니","eonni","Chị gái (nữ dùng)"),
 ("형","hyeong","Anh trai (nam dùng)"),("오빠","oppa","Anh trai (nữ dùng)"),
 ("동생","dongsaeng","Em"),("할머니","halmeoni","Bà"),
 ("할아버지","harabeoji","Ông"),("가족","gajok","Gia đình")],
"Màu sắc":[("빨강","ppalgang","Màu đỏ"),("파랑","parang","Màu xanh dương"),
 ("노랑","norang","Màu vàng"),("초록","chorok","Màu xanh lá"),
 ("검정","geomjeong","Màu đen"),("하양","hayang","Màu trắng"),
 ("보라","bora","Màu tím"),("분홍","bunhong","Màu hồng"),
 ("주황","juhwang","Màu cam"),("회색","hoesaek","Màu xám"),
 ("갈색","galsaek","Màu nâu")],
"Đồ ăn Hàn Quốc":[("밥","bap","Cơm"),("김치","gimchi","Kimchi"),
 ("물","mul","Nước"),("우유","uyu","Sữa"),("빵","ppang","Bánh mì"),
 ("사과","sagwa","Quả táo"),("바나나","banana","Chuối"),("고기","gogi","Thịt"),
 ("계란","gyeran","Trứng"),("국","guk","Canh"),
 ("커피","keopi","Cà phê"),("차","cha","Trà")],
"Phương tiện giao thông":[("버스","beoseu","Xe buýt"),("지하철","jihacheol","Tàu điện ngầm"),
 ("기차","gicha","Tàu hỏa"),("택시","taeksi","Taxi"),("비행기","bihaenggi","Máy bay"),
 ("자동차","jadongcha","Ô tô"),("자전거","jajeongeo","Xe đạp"),
 ("오토바이","otobai","Xe máy"),("정류장","jeongnyujang","Trạm xe")],
"Trường học":[("학교","hakgyo","Trường học"),("학생","haksaeng","Học sinh"),
 ("선생님","seonsaengnim","Giáo viên"),("책","chaek","Sách"),
 ("공책","gongchaek","Vở"),("연필","yeonpil","Bút chì"),
 ("볼펜","bolpen","Bút bi"),("책상","chaeksang","Bàn học"),
 ("의자","uija","Ghế"),("교실","gyosil","Phòng học"),("시험","siheom","Bài thi")],
"Thể thao":[("축구","chukgu","Bóng đá"),("농구","nonggu","Bóng rổ"),
 ("야구","yagu","Bóng chày"),("테니스","teniseu","Quần vợt"),
 ("수영","suyeong","Bơi"),("달리기","dalligi","Chạy bộ"),
 ("배구","baegu","Bóng chuyền"),("태권도","taegwondo","Taekwondo")],
"Thiên nhiên":[("산","san","Núi"),("바다","bada","Biển"),("강","gang","Sông"),
 ("나무","namu","Cây"),("꽃","kkot","Hoa"),("구름","gureum","Mây"),
 ("하늘","haneul","Bầu trời"),("비","bi","Mưa"),("해","hae","Mặt trời"),
 ("별","byeol","Ngôi sao")],
"Thời gian và ngày tháng":[("오늘","oneul","Hôm nay"),("내일","naeil","Ngày mai"),
 ("어제","eoje","Hôm qua"),("아침","achim","Buổi sáng"),
 ("저녁","jeonyeok","Buổi tối"),("밤","bam","Ban đêm"),
 ("시간","sigan","Thời gian"),("월요일","woryoil","Thứ Hai"),
 ("화요일","hwayoil","Thứ Ba"),("주말","jumal","Cuối tuần")],
"Nghề nghiệp":[("의사","uisa","Bác sĩ"),("교사","gyosa","Giáo viên"),
 ("경찰","gyeongchal","Cảnh sát"),("요리사","yorisa","Đầu bếp"),
 ("회사원","hoesawon","Nhân viên công ty"),("가수","gasu","Ca sĩ"),
 ("배우","baeu","Diễn viên"),("운전사","unjeonsa","Tài xế"),
 ("간호사","ganhosa","Y tá")],
"Chào hỏi và giới thiệu":[("안녕하세요","annyeonghaseyo","Xin chào"),
 ("감사합니다","gamsahamnida","Cảm ơn"),("죄송합니다","joesonghamnida","Xin lỗi"),
 ("반갑습니다","bangapseumnida","Rất vui được gặp"),
 ("안녕","annyeong","Chào / Tạm biệt"),("네","ne","Vâng"),
 ("아니요","aniyo","Không"),("잘 가요","jal gayo","Đi nhé")],
}
EVENT_BANKS={
"christmas":[("크리스마스","keuriseumaseu","Giáng sinh"),
 ("산타","santa","Ông già Noel"),("눈","nun","Tuyết"),
 ("선물","seonmul","Quà tặng"),("트리","teuri","Cây thông Noel"),
 ("겨울","gyeoul","Mùa đông"),("축하","chukha","Chúc mừng"),
 ("연말","yeonmal","Cuối năm"),("별","byeol","Ngôi sao"),
 ("양말","yangmal","Tất"),("카드","kadeu","Thiệp"),
 ("촛불","chotbul","Ánh nến"),("종","jong","Chuông"),
 ("눈사람","nunsaram","Người tuyết"),("장식","jangsik","Trang trí"),
 ("불빛","bulbit","Ánh đèn"),("가족","gajok","Gia đình"),
 ("친구","chingu","Bạn bè"),("노래","norae","Bài hát"),
 ("파티","pati","Bữa tiệc"),("휴일","hyuil","Ngày nghỉ"),
 ("음식","eumsik","Đồ ăn"),("케이크","keikeu","Bánh kem"),
 ("초콜릿","chokollit","Sô-cô-la")],
"new_year":[("새해","saehae","Năm mới"),("축하","chukha","Chúc mừng"),
 ("계획","gyehoek","Kế hoạch"),("소원","sowon","Điều ước"),
 ("달력","dallyeok","Lịch"),("희망","huimang","Hy vọng"),
 ("시작","sijak","Bắt đầu"),("목표","mokpyo","Mục tiêu"),
 ("건강","geongang","Sức khỏe"),("행복","haengbok","Hạnh phúc")],
"valentine":[("사랑","sarang","Tình yêu"),("선물","seonmul","Quà"),
 ("초콜릿","chokollit","Sô-cô-la"),("꽃","kkot","Hoa"),
 ("편지","pyeonji","Lá thư"),("연인","yeonin","Người yêu"),
 ("데이트","deiteu","Buổi hẹn"),("마음","maeum","Tấm lòng"),
 ("고백","gobaek","Tỏ tình")],
"children_day":[("어린이","eorini","Trẻ em"),("아이","ai","Đứa trẻ"),
 ("장난감","jangnangam","Đồ chơi"),("공원","gongwon","Công viên"),
 ("선물","seonmul","Quà"),("가족","gajok","Gia đình"),
 ("놀이","nori","Trò chơi"),("행복","haengbok","Hạnh phúc")],
"hangul_day":[("한글","hangeul","Chữ Hangeul"),("글자","geulja","Chữ cái"),
 ("모음","moeum","Nguyên âm"),("자음","jaeum","Phụ âm"),
 ("언어","eoneo","Ngôn ngữ"),("발음","bareum","Phát âm"),
 ("읽기","ilkgi","Đọc"),("쓰기","sseugi","Viết")],
"pepero":[("과자","gwaja","Bánh snack"),("초콜릿","chokollit","Sô-cô-la"),
 ("선물","seonmul","Quà"),("친구","chingu","Bạn bè"),
 ("편지","pyeonji","Lá thư"),("상자","sangja","Hộp"),
 ("가게","gage","Cửa hàng"),("기념일","ginyeomil","Ngày kỷ niệm")],
}

def construct(seed,topic,level,options,index):
    """Construct one concise vocabulary quiz with real option-specific details."""
    wrong=[options[(index+j)%len(options)] for j in (1,2,3)]
    if any(row[0]==seed[0] for row in wrong):
        wrong=[r for r in options if r[0]!=seed[0]][:3]
    return Quiz(topic=topic,question=seed[2],clue=seed[2],
        correct=seed[0],romanization=seed[1],meaning=seed[2],
        wrong1=wrong[0][0],wrong1_romanization=wrong[0][1],wrong1_meaning=wrong[0][2],
        wrong2=wrong[1][0],wrong2_romanization=wrong[1][1],wrong2_meaning=wrong[1][2],
        wrong3=wrong[2][0],wrong3_romanization=wrong[2][1],wrong3_meaning=wrong[2][2],
        level=level,explanation=seed[0]+" nghĩa là "+seed[2]+".")
 
def fill(store,topic,level,needed,event_id=None,batch_id=""):
    """Return new question IDs, no overwritten existing questions; known provenance."""
    options=EVENT_BANKS.get(event_id) if event_id else BANKS.get(topic)
    if not options or needed<=0:return []
    created=[]
    for index,seed in enumerate(options):
        q=construct(seed,topic,level,options,index)
        try:
            created.append(store.insert_question(q,provider="curated_fallback",batch_id=batch_id))
        except DuplicateQuestion:continue
        if len(created)>=needed:break
    return created

def available_bank_topics(store_counts,excluded=()):
    return sorted((t for t in BANKS if t not in excluded),
                  key=lambda t:(store_counts.get(t,0),t))
