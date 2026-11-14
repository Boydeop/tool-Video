# v1.3.2 — Chuông, cụm từ theo sự kiện và bổ sung câu thiếu

## Phản hồi từ Windows

Ảnh v1.3.1 có câu “Chuông → 종” bị từ chối do so nghĩa; nhiều cụm như
“크리스마스 양말”, “크리스마스 모자” thiếu nguyên mục từ. Autopilot có 3/5 câu đạt
nhưng không tạo MP4 vì chưa đủ số câu yêu cầu. Một lượt cũ báo AI không khả dụng.
Không có dữ liệu XML thực từ máy người dùng, nên ảnh chưa xác định được chính xác
bản dịch KRDict đã trả cho 종; bản sửa bổ sung chẩn đoán nghĩa thật để tránh đoán.

## Bản sửa

- So nghĩa bỏ tiền tố phân loại “cái/chiếc” ở đầu cụm có danh từ theo sau, nhưng
  giữ từ đứng riêng và các cấu trúc “cái tôi/cái gì/cái này/cái kia”. Không bỏ dấu
  tiếng Việt hay hạ ngưỡng so khớp chung.
- `lookup_evidence` tra cả cụm trước. Nếu thiếu, chỉ xét 2–3 từ Hangul có khoảng
  trắng, yêu cầu từng thành phần có mục từ chính xác và bản dịch. Thành phần có
  nhãn từ loại phải là danh từ. Không đoán cách chẻ một chuỗi Hangul viết liền.
- Toàn bộ cụm vẫn phải khớp câu hỏi và vượt qua AI hỏi ngược. `exact` của cả cụm
  vẫn false; báo cáo lưu thành phần/mã mục từ và ghi tầng bằng chứng riêng
  `dictionary_components+blind_ai`. Không coi sự có mặt của các từ là chứng minh
  đầy đủ nghĩa một thành ngữ hoặc cụm bất kỳ.
- Luồng tự sửa cũng dùng bằng chứng thành phần. Nghĩa của mục từ nguyên vẹn lấy
  từ KRDict; nghĩa cụm do AI ghép, giữ thứ tự tiếng Việt và ghi rõ nguồn hỗ trợ.
- Báo cáo QA/sửa hiện nghĩa đang cần và các bản dịch KRDict thực trả khi không khớp.
- Autopilot thêm lượt sinh bổ sung thứ ba khi còn thiếu, giữ ngân sách 210 giây,
  không bỏ toàn bộ lượt ngay khi một lần trả về rỗng do trùng. Nếu có lỗi provider,
  dừng thay vì lặp lỗi API. Vẫn không hạ số câu yêu cầu hoặc xuất câu không đạt.
- Khi không có provider AI được cấu hình, báo cụ thể vào API & Model thay vì
  chuỗi “All providers unavailable:” trống. Bản sửa không thay đổi quota nhà cung cấp.

## Kiểm chứng

Thêm regression dùng dữ liệu giả lập có cấu trúc KRDict: chuông/cái chuông, danh từ
phân loại, vớ Giáng sinh, từ loại, thiếu thành phần, định nghĩa không thay bản dịch,
đáp án nhiễu đồng nghĩa, AI phản đối, tự sửa cụm và giới hạn tra từ. Có bài Autopilot
bổ sung từ 2+1+2 câu rồi mới gọi render đủ 5 câu. Không thử key thật trên máy người dùng.

Sau cập nhật: chọn các câu lỗi cũ và chạy Kiểm tra & tự sửa hoặc thử Autopilot lại.
Bằng chứng cache được đọc lại qua logic mới; không phải xóa dữ liệu hoặc nhập lại key.
Nếu provider hết quota hoặc từ/cụm vẫn thiếu bằng chứng, dữ liệu cũ được giữ và lý do
thực được hiển thị; không cam kết mọi chủ đề sẽ luôn tạo đủ video.
