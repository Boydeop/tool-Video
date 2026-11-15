# v1.4.2 — Lượt sản xuất chỉ hoàn tất 1/3 video

Ảnh lỗi ở v1.4.1 cho thấy hai nguyên nhân độc lập:

- Cơ thể và sức khỏe: chưa đủ câu đạt QA strict. KRDict không nhận nguyên cụm
  `기침을 하다`, `배가 아프다`; logic cũ chỉ ghép được cụm danh từ.
- Văn hóa ẩm thực: Gemini trả HTTP 503. Đây là dịch vụ tạm thời không sẵn sàng,
  không chứng minh key sai hoặc ứng dụng đang treo. Sau giới hạn thử lại, lượt
  sản xuất kết thúc một phần và giữ video đã xong.

## Thay đổi

1. Với mẫu tân ngữ + 하다 có trợ từ đúng, tra nguyên động từ ghép trong KRDict:
   `기침을 하다` → `기침하다`, `재채기를 하다` → `재채기하다`.
   Chỉ có danh từ + nghĩa của 하다 chưa đủ để thông qua.
2. Với chủ ngữ + tính từ dạng từ điển, tra riêng danh từ và tính từ cùng mã
   mục từ: `배가 아프다` → `배` + `아프다`. Không đoán đuôi chia, không coi mọi
   chuỗi từ có mục từ là cụm hợp lệ. Giữ nhãn bằng chứng thành phần và vẫn
   cần AI hỏi ngược, đối chiếu nghĩa và kiểm tra đáp án nhiễu.
3. Sinh câu ở chế độ strict ưu tiên lựa chọn dạng mục từ từ điển, áp dụng cho
   cả bốn đáp án; lỗi QA trước được đưa vào yêu cầu tiếp theo để tránh sinh
   lại cùng dạng không thể kiểm chứng. Hướng dẫn chỉ tồn tại trong tác vụ.
4. Thử tới provider thứ ba đã cấu hình khi hai provider đầu lỗi, trong ngân
   sách thời gian hiện có. Không tự thêm tài khoản, thay model hoặc chọn model
   trả phí. Nếu chỉ cấu hình Gemini thì không tự có provider dự phòng.
5. Tôn trọng Retry-After dạng giây/ngày giờ khi 429/5xx; 429 vẫn nghỉ toàn
   provider. Thông báo 5xx bằng tiếng Việt, phân biệt lỗi dịch vụ với thiếu key.
6. Bỏ câu thông báo luôn hứa sẽ tạo lại dù tác vụ đã hết lượt thử. MP4 chưa
   đạt QA vẫn không được xuất, video đã xong không dựng lại khi Tiếp tục.

## Kiểm chứng và giới hạn

Thêm kiểm thử ngoại tuyến cho đúng các cụm trong ảnh, câu có trợ từ sai,
thiếu bằng chứng, đuôi chia, đáp án nhiễu đồng nghĩa, thiếu AI reviewer,
ngữ cảnh sinh câu, fallback provider thứ ba và Retry-After.
Linux: 226 test chạy trong 40,670 giây, 225 đạt và 1 bỏ qua vì cần Windows
DPAPI thật. Build Windows/EXE được kiểm chứng riêng trong GitHub Actions.

Không truy cập được khóa hay database trên máy Windows trong ảnh; chưa
kiểm chứng trực tiếp câu do tài khoản đó sinh ra. Sửa mã không thể bảo đảm
Gemini hết 503. Khi chỉ cấu hình một provider và dịch vụ tiếp tục lỗi, cần
chờ rồi bấm **Tiếp tục mục chưa xong**, hoặc cấu hình provider dự phòng trong
**API & Model**. Các cấu trúc ngoài phạm vi bằng chứng vẫn cần duyệt/sửa.
