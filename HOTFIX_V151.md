# v1.5.1 — QA sở thích, reviewer lỗi và nút sản xuất

## Các ảnh v1.5.0 cho thấy gì?

1. `사진찍기`, `그림그리기`, `음악감상`, `영화감상` có thể là nhãn hoạt động
   viết liền, không phải nguyên mục từ mà KRDict trả về. Bản cũ chỉ phân tích
   cụm có khoảng trắng nên thiếu bằng chứng, rồi AI có thể đề xuất đổi từ quá xa.
2. “Nghe nhạc” và “thưởng thức âm nhạc” có cách nói khác nhau; so token thuần
   có thể đánh trượt. Nhưng “âm nhạc” riêng lẻ không phải “nghe nhạc”.
3. Những đề xuất như `산책` cho “du lịch”, hay “bơi lội” cho “thể thao”
   là không đủ tương đương. Việc từ chối các đề xuất này là đúng, không nên
   sửa bằng cách hạ tiêu chuẩn để đủ số video.
4. Ảnh lượt sản xuất báo Gemini HTTP 503 và hết ba lần thử. Đây là lỗi dịch vụ
   AI; ảnh không cho thấy lỗi encoder/FFmpeg. Không có câu đạt thì không xuất MP4.
5. Sau tác vụ tự sửa câu, `loadStatus()` thấy cờ activeRender còn true và khóa
   nút sản xuất. `finally` xóa cờ nhưng không cập nhật nút đó, nên nút có thể
   giữ trạng thái disabled đến lần tải status tiếp theo.

## Sửa trong bản này

- `studio/krdict.py`: nhận diện các cấu trúc sở thích viết liền có đuôi rõ
  `찍기`, `그리기`, `하기`, `감상`; tra từng danh từ/động từ KRDict thật.
  Chẳng hạn `사진찍기` được đối chiếu `사진` + `찍다`. Đây là bằng chứng thành
  phần, không khai là nguyên mục từ. Không thử chia âm tiết tùy ý cho mọi từ;
  kiểm thử cũ về giới hạn tra cứu vẫn được giữ nguyên.
- Chỉ thêm tập tương đương đóng cho nghe nhạc/thưởng thức âm nhạc,
  xem phim/thưởng thức phim, chụp ảnh/chụp hình. Không coi du lịch=đi dạo,
  thể thao=bơi lội, ảnh=chụp ảnh hay âm nhạc=nghe nhạc.
- `studio/repair.py`: ưu tiên sửa nhãn nghĩa gần khớp bằng gloss KRDict,
  giữ nguyên bốn từ Hàn. Ví dụ đáp án nhiễu `사진` phải hiển thị “ảnh”, không
  phải “chụp ảnh”. Chỉ ghi bản sửa sau khi strict QA + blind reviewer đạt;
  vẫn giữ lịch sử trước/sau. Nhãn không liên quan không được tự đoán.
- `studio/review.py`, `studio/service.py`: reviewer lỗi có thông tin lỗi và
  thời gian thử lại; không viết lại đáp án nếu nguyên nhân là reviewer mất
  kết nối/key/503. Reviewer và proposer có thể dùng provider thứ ba đã cấu hình,
  vẫn nằm trong ngân sách thời gian hiện có và tôn trọng hạn mức.
- `studio/ai.py`: hướng dẫn sinh giữ đúng hoạt động, ưu tiên khoảng trắng rõ;
  không đổi nội dung để qua cổng từ điển.
- `studio/ui.html`: đồng bộ nút sản xuất/dọn cache khi busy/activeRender đổi,
  kể cả sau khi tác vụ repair hoàn tất.

## Kiểm chứng

- Chín hồi quy mới trong `tests/test_hobby_screenshot.py`: ảnh chụp, vẽ tranh,
  nghe nhạc/xem phim, đề xuất sai nghĩa vẫn bị chặn, phân tích có giới hạn,
  sửa nhãn không thay từ, reviewer fallback thứ ba và giữ nguyên nội dung khi lỗi.
- Chromium headless chạy cùng luồng repair với API fixture: trước sửa,
  `autoStart.disabled=True` sau khi hoàn tất; sau sửa là `False`.
- Suite local: **258 test, 257 đạt, 1 skipped dành cho Windows DPAPI**.
  Workflow Windows chạy suite và smoke test khởi chạy EXE thật; kết quả cuối cùng
  được xác nhận riêng trong chat và GitHub Actions.

## Dùng bản mới

Đóng EXE v1.5.0, sao lưu thư mục dữ liệu rồi mở v1.5.1. Trong Kho video, chọn
các câu chưa đạt và kiểm tra/tự sửa lại; bản cũ không tự được chuyển thành đạt.
Nếu chỉ có Gemini và dịch vụ vẫn trả 503, chờ phục hồi hoặc cấu hình một
provider dự phòng có quyền/model hoạt động. Thêm key cùng provider không phải
cách vượt quota hoặc bảo đảm tránh được lỗi dịch vụ.

Không dùng token/API của máy người dùng trong các kiểm thử. Chưa xác minh
phản hồi KRDict/AI thật của các câu trong ảnh; fixture offline không bảo đảm
mọi câu sẽ đạt trên dịch vụ live. Bản sửa không bảo đảm mọi cách diễn đạt cùng
nghĩa đều được nhận diện, mọi phiên âm đều đúng hoặc không còn HTTP 503.
