# v1.3.1 — Duyệt tay và xuất các câu đã đạt QA

## Lỗi tái hiện

Ảnh Windows v1.3.0 có 8 câu `auto_approved`, cả 8 được chọn và sẵn sàng xuất,
nhưng nút Duyệt bị khóa. `updateSelection()` và click handler chỉ nhận draft/rejected,
loại câu đã kiểm tra máy. Nút chọn câu đã duyệt chỉ chọn trạng thái approved.
Các nút xuất lại nằm trong details đang đóng, khiến đường từ QA đến video không rõ.

## Thay đổi

- Cho phép duyệt tay `auto_approved → approved`; backend vốn hỗ trợ chuyển đổi này.
  Giữ nguyên bằng chứng QA và toàn bộ lựa chọn khi có cả câu duyệt tay lẫn kiểm tra máy.
- Nút Chọn câu đã duyệt chọn được cả hai nhóm, vẫn tuân theo bộ lọc lô và bỏ qua câu đã xuất.
- Thêm Làm video ngay tại ngân hàng; dùng thiết lập Câu/MP4 hiện có để chia video.
- Mở sẵn mục xuất; khi bắt đầu xuất tự cuộn đến tiến trình.
- Nút duyệt/xuất vẫn bị khóa khi đang xử lý hoặc chỉ chọn câu đã xuất.

## Kiểm thử mới

- Chạy chính các hàm JavaScript của UI trong Node với 8 câu auto_approved:
  bật nút, gửi đủ ID duyệt, giữ lựa chọn, gọi xuất đủ 8 câu, mở tiến trình;
  chọn chung hai trạng thái theo lô, giữ lựa chọn hỗn hợp và chặn tác vụ bận/đã xuất.
- HTTP → duyệt 8 câu → queue chia 6+2 → hai kết quả → đánh dấu cả 8 đã xuất.
  Renderer được giả lập trong bài này; suite render thật hiện có vẫn được chạy.
- Không thay đổi logic QA, tự sửa, key hoặc video cũ.
