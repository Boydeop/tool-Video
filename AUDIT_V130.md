# v1.3.0 — Tự sửa câu bằng KRDict + AI

## Hành vi mới

Nút **Kiểm tra & tự sửa 6 câu AI mới** thay thế nút chỉ kiểm chứng. Có thêm
**Tự sửa câu đã chọn** (1–6 câu). Tác vụ chạy nền, hiển thị giai đoạn, tiếp tục chạy
khi tải lại trang; kết quả và lịch sử sửa được lưu trong SQLite.

Autopilot cũng thử sửa câu không đạt trước khi bỏ qua câu đó. Mỗi câu thử tối đa
hai provider đã cấu hình, nằm trong ngân sách QA hiện có. Không đổi câu hỏi tiếng
Việt để hợp thức hóa một đáp án sai.

## Cách dùng từ điển để sửa

1. Đọc nguyên câu và bằng chứng KRDict hiện có.
2. AI đề xuất lại từ Hàn cùng nghĩa/phiên âm của cả bốn lựa chọn.
3. Tra mới KRDict cho từng từ đề xuất, yêu cầu mục từ chính xác và `trans_word`
   khớp nghĩa cần dùng. Nghĩa được ghi vào bản sửa lấy từ bản dịch KRDict đó.
4. Chạy lại kiểm tra cấu trúc, từ điển cho bốn lựa chọn và AI hỏi ngược ở chế độ strict.
5. Chỉ khi đạt mới cập nhật từ Hàn, nghĩa, phiên âm, giải thích, hash đáp án và QA
   trong một transaction; chuyển thành `auto_approved`. Lưu đầy đủ trước/sau và bằng chứng.

Không gán cứng `루돌프 → 순록` trong thuật toán sửa. Cùng luồng đã được thử với trường
hợp “Nước” bị gán sai sang `학교`, để xác nhận đây là chức năng sửa tổng quát.
AI đề xuất ứng viên vì API tra mục từ Hàn không phải công cụ dịch ngược tiếng Việt
được bảo đảm. KRDict cung cấp mục từ/nghĩa xác nhận; phiên âm Latin do AI đề xuất,
kiểm tra định dạng và được ghi rõ nguồn, không tuyên bố là phiên âm do KRDict cấp.

## Bảo toàn dữ liệu

- Không sửa câu đã xuất thành video hoặc đã bị loại.
- Không lưu một đề xuất chưa đạt QA; thiếu key, lỗi mạng, thiếu mục từ hoặc AI
  chưa xác nhận thì giữ nguyên nội dung cũ và trả lý do.
- Kiểm tra trùng đáp án và so lại nội dung gốc trong transaction để không ghi đè
  chỉnh sửa diễn ra trong lúc chờ API. Lỗi rollback cả câu/QA/history.
- Schema `question_revisions` được bổ sung không xóa câu, video hoặc dữ liệu cũ.
- Khóa media ngăn render cùng lúc một câu đang sửa trong Service.
- Trong ngân hàng có **Lịch sử tự sửa** và phần **xem trước/sau** trên câu đã sửa.
- Khi bộ lọc Cần QA không còn câu, giao diện chỉ dẫn sang Đã duyệt thay vì bảo tạo
  lại dữ liệu đang tồn tại.

## Kiểm chứng

20 kiểm thử mới: sửa tổng quát, nghĩa lấy từ KRDict, thiếu key, từ bịa, định nghĩa
không thay bản dịch, sai nghĩa đáp án nhiễu, AI phản đối, deadline/cancel, lưu câu/hash/
QA/history nguyên tử, trùng đáp án, chỉnh sửa đồng thời, bảo vệ câu đã render,
migration, HTTP chạy nền, hàng đợi độc quyền và Autopilot sửa xong trước render.
Các cuộc gọi AI/KRDict trong suite dùng mock; chưa dùng key thật trên máy người dùng.

Windows workflow kiểm tra cả control sửa câu và endpoint trong EXE đã đóng gói.
Sau cập nhật, chọn câu cũ đang lỗi → **Tự sửa câu đã chọn**; không cần tạo lại cả lô.
