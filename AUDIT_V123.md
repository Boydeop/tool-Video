# v1.2.3 — QA KRDict nhầm định nghĩa với bản dịch

Ảnh phản hồi từ Windows v1.2.2 cho thấy câu “Mùa đông → 겨울” bị chặn vì các
đáp án 봄 và 가을 bị coi là cũng đúng. Câu “Tuần lộc → 루돌프” không tìm thấy mục từ.
Ảnh này hiển thị kết quả kiểm duyệt nội dung, không phải thông báo API key bị từ chối.

## Nguyên nhân và bản sửa

- `studio/krdict.py` từng trộn `trans_word` và `trans_dfn` vào cùng danh sách nghĩa.
  Định nghĩa mùa xuân/mùa thu có thể nhắc đến mùa đông, nhưng không có nghĩa chúng
  đồng nghĩa. Parser mới lưu riêng `glosses` và `definitions`.
- `studio/review.py` chỉ dùng bản dịch mục từ `glosses` làm bằng chứng khớp nghĩa;
  vẫn lưu định nghĩa để xem lại. Nếu chỉ có định nghĩa, strict không tự chứng nhận.
- Điểm khớp trước đây chỉ đo phần từ trong câu hỏi được tìm thấy ở chuỗi ứng viên.
  Một đoạn mô tả dài chứa đầy đủ từ câu hỏi có thể đạt 100%. Nay dùng giao/hiệp token
  hai phía và không xem chú thích trong ngoặc là một bản dịch đồng nghĩa riêng.
- Cache tăng phiên bản từ 2 lên 3 để tự tải lại dữ liệu có cấu trúc mới, không yêu cầu
  xóa database, video hoặc nhập lại key.
- Prompt tạo quiz ưu tiên từ phổ thông, tránh dùng tên riêng thay tên loài.
  `루돌프` là tên Rudolph; từ chung chỉ tuần lộc là `순록`. Không tự sửa câu đã lưu
  trên máy người dùng hoặc tự duyệt một tên riêng không có bằng chứng.

## Kiểm thử và sử dụng

Thêm 6 test bằng XML giả lập cấu trúc KRDict: phân biệt gloss/definition, bốn mùa,
đáp án nhiễu thực sự đồng nghĩa, định nghĩa thiếu bản dịch, tên riêng thiếu mục từ,
và chú thích không phải đồng nghĩa. Không có key thật hay dữ liệu API live trong fixture.

Sau khi cập nhật EXE: đóng bản cũ, mở v1.2.3 rồi bấm **Kiểm chứng 8 câu AI mới bằng
KRDict + hỏi ngược**. Câu “Mùa đông” sẽ được kiểm tra lại bằng dữ liệu tách nghĩa;
chỉ đạt khi mọi bằng chứng từ điển và AI đạt. Câu Rudolph cần tạo lại với từ đúng;
không bấm duyệt để bỏ qua cảnh báo nội dung.

Kết quả test và bản build được xác minh trong phiên phát hành; không có khẳng định
đã truy cập database hay thử key trên máy Windows của người dùng.
