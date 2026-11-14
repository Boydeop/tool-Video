# Audit v1.4.0 · Sản xuất tự động và bố cục giao diện

Phạm vi: điều phối nhiều video, sinh/kiểm chứng/sửa câu, lưu SQLite, khôi phục
lượt chạy, xuất MP4, HTTP, kho video, thao tác bản nháp và đóng gói Windows.
Các audit KRDict trước vẫn áp dụng: [v1.2.2](AUDIT_V122.md),
[v1.3.0](AUDIT_V130.md), [v1.3.1](AUDIT_V131.md), [v1.3.2](AUDIT_V132.md).

## 🔴 1. Lỗi nghiêm trọng và nguyên nhân

| Lỗi | Vì sao xảy ra / file | Sửa trong bản này |
| --- | --- | --- |
| Video thiếu câu thì kết thúc lượt, đẩy câu lỗi ra ngân hàng | `studio/autopilot.py`, `studio/service.py`: sinh dư câu rồi lưu cùng bản nháp thủ công; thiếu cơ chế thử lại toàn bộ video | Mỗi video có vùng câu tạm riêng. Không đủ QA hoặc dựng lỗi thì bỏ vùng tạm, tạo lại video đó |
| Không thể tiếp tục đúng video sau khi đóng/crash | `studio/exports.py`: trạng thái điều phối nằm trong bộ nhớ; `production_runs` chỉ lưu tổng kết | `production_tasks` lưu từng video, số lần thử, thời điểm thử lại, tên đầu ra và mốc hoàn tất |
| Có thể dựng lại video đã xong nếu lỗi xảy ra cuối bước | Xuất file, cập nhật câu và tổng kết là các bước tách rời | `Store.mark_rendered` ghi registry, trạng thái câu và trạng thái video hoàn tất trong cùng giao dịch. Lỗi callback/SEO sau đó không xóa mốc này |
| HTTP 503 bị xử lý như câu từ vựng sai | Lỗi API sinh câu, AI hỏi ngược và KRDict bị mất thông tin khi chuyển sang báo cáo QA | Truyền `retry_after`/`blocked` qua các tầng. Lỗi dịch vụ thì chờ và tạo lại; thiếu key/quyền thì dừng để sửa kết nối, không chạy hết 100 mục |
| Câu thừa cũ không có lối xử lý/xóa | `studio/database.py`, `studio/webapp.py`, `studio/ui.html` chưa có vòng đời xóa/khôi phục | Thùng rác, khôi phục, xóa vĩnh viễn; tự chuyển câu AI thừa thuộc lô đã xuất vào thùng rác một lần khi nâng cấp |
| Câu đã xóa vẫn chặn sinh câu mới | Hash câu/đáp án vẫn chiếm ràng buộc UNIQUE | Giải phóng hash khi chuyển vào thùng rác; khôi phục kiểm tra xung đột và không ghi đè câu mới |
| Không đáp ứng lượt 100 video | `Plan` giới hạn 12; bộ chọn chủ đề yêu cầu tất cả chủ đề khác nhau; kho chỉ lấy 80 video | Cho 1–100 video/lượt, sử dụng lại tập chủ đề sau khi hết; vẫn kiểm tra trùng nội dung. API kho lấy tới 1.000 mục, giao diện phân trang 20 |
| File MP4 tạm có thể bị nhìn như thành phẩm | Kho quét thư mục trực tiếp, kể cả output chưa đăng ký | Tên output dành riêng từng tác vụ; ẩn output chưa hoàn tất và dọn orphan trước/sau lần thử lỗi |

## 🟡 2. Tối ưu đã áp dụng

- Chạy tuần tự một video tại một thời điểm để giới hạn RAM, FFmpeg và tải API.
  “100 video/lượt” là hàng đợi 100 video, không mở 100 encoder đồng thời.
- Backoff tăng dần cho lỗi riêng từng video; 429/503/mạng áp dụng thời gian chờ
  chung cho hàng đợi, tránh dồn yêu cầu vào dịch vụ đang lỗi.
- Mặc định tối đa 3 lần thử/video; có thể chọn 2/3/5. Hết lượt thử thì hiện đúng
  video chưa xong và nút tiếp tục; không lặp vô hạn hoặc báo đủ MP4 khi thiếu.
- Không kiểm chứng câu dư sau khi đã đủ; xóa câu tạm dư ngay khi hoàn tất.
- Index truy vấn tác vụ đến hạn và câu theo tác vụ; giảm tần suất cập nhật tiến
  trình, chỉ ghi tổng kết SQLite tại các lần chuyển trạng thái.
- Kho video phân trang, thumbnail tải khi cần; không dựng toàn bộ 100 dòng video
  trên cùng trang.

## 🟢 3. Refactor và audit bố cục

- `studio/batch_store.py`: schema, claim giao dịch, retry/resume và thùng rác.
- `studio/batch_runner.py`: vòng đời từng video, chờ lỗi, cleanup, checkpoint.
- `studio/exports.py`: adapter luồng chạy/HTTP status; giữ pipeline kiểm chứng
  hiện có trong `autopilot.py` cho từng video.
- Dùng `ContextVar` để gắn câu sinh tự động với đúng tác vụ mà không trộn với
  yêu cầu tạo câu thủ công từ luồng khác.
- Sidebar còn **Sản xuất → Kho video → API & Model → Đăng video → Thiết lập &
  dọn dẹp**. Sản xuất chính chỉ hiện số video, số câu, giọng đọc, SEO và profile.
- Cấp độ, dạng bài, template, sự kiện, mức QA, số lần thử nằm trong phần nâng cao.
  Tạo câu thủ công nằm trong phần đóng riêng ở trang Sản xuất.
- Kho video ưu tiên MP4 thành phẩm. Bản nháp và công cụ chỉnh câu nằm trong phần
  nâng cao; gộp các nút chọn câu thành một menu, giữ một hành động tự sửa và một
  hành động làm video. Không lặp danh sách lịch sử tại trang dọn dẹp.
- Câu duyệt tay và câu kiểm tra máy cùng thuộc bộ lọc “sẵn sàng”; vẫn có thể duyệt
  tay hoặc làm video trực tiếp, giữ bản sửa lỗi 8 câu ở v1.3.1.

## 🛠️ 4. Kiểm chứng và giới hạn

- Linux: chạy đủ 192 kiểm thử trong 46 giây, 191 đạt, 1 bỏ qua vì cần Windows
  DPAPI thật. Windows CI chạy lại ca DPAPI trên hệ điều hành tương ứng.
- Bộ kiểm thử bao gồm điều phối 100 video với lỗi encoder chủ động: 12 video lỗi
  lần đầu, 112 lần thử, 100 output duy nhất, không có câu tạm trong ngân hàng.
- Kiểm tra tạm dừng, mở lại, tiếp tục; bỏ câu tạm sau crash; hết lượt thử rồi thử
  lại; lỗi sau commit không tạo MP4 trùng; lỗi 503 chờ chung; thiếu key dừng sớm.
- Hai video được dựng bằng FFmpeg thật ở 540p/24fps, không giọng đọc; FFprobe
  kiểm tra stream, thời lượng và metadata. AI/QA của ca này là dữ liệu fixture.
- Kiểm tra HTTP xóa/khôi phục/resume; migration câu thừa cũ và rollback khi chọn
  lẫn câu đã xuất. Lịch sử sửa được giữ trong thùng rác tới khi xóa vĩnh viễn.
- Chromium chạy giao diện thật: 100 tác vụ mô phỏng AI/QA/encoder, đủ 100 video
  trong kho, 20 mục/trang, xóa/khôi phục bản nháp, không lỗi JavaScript. Đã xem
  bố cục desktop và mobile. Đây không phải 100 lượt gọi API hoặc encode đầy đủ.
- CI Windows chạy lại bộ test, đóng gói PyInstaller kèm FFmpeg/FFprobe, khởi chạy
  EXE và smoke test HTTP/control/schema trước khi phát hành.

Không thể bảo đảm API ngoài luôn khả dụng hoặc câu AI nào cũng đạt KRDict. Bản
này bảo đảm lỗi được giữ ở tác vụ có thể tiếp tục, thay vì đẩy câu lỗi cho người
dùng xử lý. Lỗi xác thực cần sửa key/model; lỗi quota có thể cần chờ lâu hơn các
lần thử tự động. Tắt máy giữa encode có thể để lại file tạm, được ẩn và dọn ở lần
tiếp tục. Mốc hoàn tất dùng giao dịch SQLite; chưa có cơ chế fsync nguyên khối
cho cả filesystem và SQLite khi mất điện đột ngột. Không thay đổi quyền đăng
video: vẫn cần kết nối và thao tác/lịch đăng riêng.

## Dùng bản mới

1. Đóng EXE cũ rồi mở EXE mới; giữ nguyên thư mục dữ liệu để nhận cấu hình và
   tiến trình. Có thể sao lưu `%LOCALAPPDATA%\TiengHanSoEasyStudio` trước nâng cấp.
2. Vào **Sản xuất**, nhập 1–100 video rồi bấm **Bắt đầu sản xuất**.
3. Nếu dịch vụ chưa phục hồi sau hết lượt thử, bấm **Tiếp tục mục chưa xong**
   ở chính lượt đó. Video đã hoàn tất không dựng lại.
4. Vào **Kho video** để lấy MP4. Dùng **Dọn bản nháp AI chưa dùng** cho những lô
   cũ chưa có video nào; các bản nháp đó có thể khôi phục từ **Thùng rác**.
