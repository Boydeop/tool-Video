"""Korean quiz validation and curriculum rotation."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from difflib import SequenceMatcher
import hashlib
import random
import re
import unicodedata

TOPICS = [
    "Chào hỏi và giới thiệu", "Gia đình", "Số đếm thuần Hàn",
    "Số đếm Hán Hàn", "Thời gian và ngày tháng", "Đồ ăn Hàn Quốc",
    "Gọi món nhà hàng", "Mua sắm và giá cả", "Quần áo",
    "Màu sắc", "Thời tiết", "Phương tiện giao thông",
    "Hỏi đường", "Trường học", "Lớp học tiếng Hàn",
    "Văn phòng và công việc", "Phỏng vấn xin việc", "Nghề nghiệp",
    "Động từ sinh hoạt", "Tính từ miêu tả", "Cơ thể và sức khỏe",
    "Bệnh viện và hiệu thuốc", "Sở thích", "Thể thao",
    "Du lịch và khách sạn", "Sân bay và nhập cảnh", "Địa điểm thành phố",
    "Đồ dùng gia đình", "Động vật", "Thiên nhiên",
    "Hội thoại ở cửa hàng tiện lợi", "Lời xin lỗi và cảm ơn",
    "Kính ngữ cơ bản", "Trợ từ 은/는", "Trợ từ 이/가",
    "Trợ từ 을/를", "Trợ từ 에/에서", "Thì hiện tại -아요/어요",
    "Thì quá khứ -았/었어요", "Thì tương lai -(으)ㄹ 거예요",
    "Cấu trúc -고 싶다", "Cấu trúc -(으)ㄹ 수 있다",
    "Cấu trúc -아/어서", "Cấu trúc -지만",
    "Cấu trúc -(으)면", "Câu mệnh lệnh và đề nghị",
    "Nghe hiểu TOPIK I", "Đọc hiểu TOPIK I",
    "Từ vựng TOPIK II", "Từ đồng nghĩa và trái nghĩa",
    "Kết hợp từ tự nhiên", "Văn hóa ẩm thực Hàn",
    "Lễ hội và truyền thống", "Ứng xử nơi công cộng",
    "Mạng xã hội và công nghệ", "Thuê nhà và chuyển nhà",
    "Ngân hàng và chuyển tiền", "Hẹn gặp bạn bè",
    "Điện thoại và nhắn tin", "Công sở Hàn Quốc",
]
LEVELS = ("TOPIK 1", "TOPIK 2", "TOPIK 3", "TOPIK 4")
FORMATS = ("từ vựng", "ngữ pháp", "hội thoại", "điền từ", "ngữ cảnh")

def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", str(value)).casefold().strip()
    return re.sub(r"\s+", " ", re.sub(r"[!?.,;:…\"'“”‘’()\[\]{}]", " ", value)).strip()

def fingerprint(value: str) -> str:
    return hashlib.sha256(normalize(value).encode("utf-8")).hexdigest()

def similar(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize(a), normalize(b)).ratio()

@dataclass(frozen=True)
class Quiz:
    topic: str
    question: str
    correct: str
    wrong1: str
    wrong2: str
    wrong3: str
    romanization: str = ""
    explanation: str = ""
    level: str = "TOPIK 1"
    meaning: str = ""
    clue: str = ""

    @property
    def short_clue(self) -> str:
        """Short screen/audio prompt, never the original verbose question."""
        if self.clue.strip():
            return self.clue.strip()
        text=self.question.strip()
        # Existing 0.4.x quiz records often contain a quoted Vietnamese clue.
        matches=re.findall(r"['“‘\"]([^'”’\"]{2,65})['”’\"]",text)
        if matches:
            return matches[-1].strip().capitalize()
        patterns=[
          r"(?i)^từ (?:tiếng hàn )?nào (?:có nghĩa là|chỉ|dùng để chỉ)\s+(.+?)\??$",
          r"(?i)^từ (.+?) (?:trong|bằng) tiếng hàn (?:là gì|nói thế nào)\??$",
          r"(?i)^(.+?) (?:trong|bằng) tiếng hàn (?:là gì|nói thế nào)\??$",
          r"(?i)^từ nào (?:chỉ|có nghĩa là)\s+(.+?)\??$",
        ]
        for pattern in patterns:
            match=re.match(pattern,text)
            if match:
                candidate=match.group(1).strip(" ?.,'“”‘’")
                if 2<=len(candidate)<=65:
                    return candidate[0].upper()+candidate[1:]
        # For older complex grammar prompts, keep context rather than cutting mid-sentence.
        return text

    @property
    def vietnamese_meaning(self) -> str:
        if self.meaning.strip():
            return self.meaning.strip()
        if self.clue.strip():
            return self.clue.strip()
        clue=self.short_clue
        # Do not relabel a full legacy question as a verified translation.
        if clue!=self.question and not clue.endswith("…"):
            return clue
        match=re.search(r"nghĩa là\s+['“‘]?(.+?)['”’]?(?:\.|$)",self.explanation,re.I)
        return match.group(1).strip(" .'“”‘’") if match else ""


    def validate(self) -> None:
        if not self.topic.strip() or not self.question.strip():
            raise ValueError("Cần chủ đề và câu hỏi.")
        answers = [self.correct, self.wrong1, self.wrong2, self.wrong3]
        if any(not a.strip() for a in answers):
            raise ValueError("Cần đủ bốn đáp án.")
        if len({normalize(a) for a in answers}) != 4:
            raise ValueError("Có hai đáp án giống nhau.")
        if not any(re.search(r"[\uac00-\ud7a3]", a) for a in answers):
            raise ValueError("Đáp án phải chứa tiếng Hàn.")
        if len(self.question) > 130 or any(len(a) > 75 for a in answers) or len(self.clue)>65 or len(self.meaning)>80 or len(self.romanization)>100:
            raise ValueError("Nội dung quá dài để hiển thị trên video.")
        if self.level not in LEVELS:
            raise ValueError("Cấp độ TOPIK không hợp lệ.")

    @classmethod
    def parse(cls, raw: dict, topic: str = "", level: str = "TOPIK 1") -> "Quiz":
        fields = {k: str(raw.get(k) or "").strip() for k in
                  ("topic", "question", "correct", "wrong1", "wrong2",
                   "wrong3", "romanization", "explanation", "level", "meaning", "clue")}
        fields["topic"] = fields["topic"] or topic
        fields["level"] = fields["level"] or level
        obj = cls(**fields)
        obj.validate()
        return obj

    def to_dict(self) -> dict:
        return asdict(self)

def daily_plan(day: date, topic_counts: dict[str, int] | None = None) -> dict:
    """Prefer under-used curriculum subjects; deterministic for a given day."""
    counts = topic_counts or {}
    seed = day.toordinal()
    randomizer = random.Random(seed)
    minimum = min(counts.get(t, 0) for t in TOPICS)
    candidates = [t for t in TOPICS if counts.get(t, 0) == minimum]
    topic = randomizer.choice(candidates)
    return {"date": day.isoformat(), "topic": topic,
            "level": LEVELS[(seed // 3) % len(LEVELS)],
            "format": FORMATS[seed % len(FORMATS)]}

def choose_random_topic(topic_counts: dict[str, int] | None = None,
                        exclude: str | None = None, rng=None) -> str:
    """Choose a random topic, weighting underused areas more heavily.

    'exclude' prevents immediate repetition when pressing Reroll.
    Uniqueness of every question is not mathematically guaranteed.
    """
    source = rng if rng is not None else random.SystemRandom()
    counts = topic_counts or {}
    options = [topic for topic in TOPICS if topic != exclude] or TOPICS[:]
    weights = [1.0 / ((1 + max(0, counts.get(topic, 0))) ** 1.5)
               for topic in options]
    return source.choices(options, weights=weights, k=1)[0]
