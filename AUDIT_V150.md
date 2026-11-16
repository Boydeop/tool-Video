# Audit repository và bản v1.5.0

Ngày: 16/11/2026. Phạm vi: toàn bộ source Python trong `studio/`, CLI,
launcher Windows, dashboard HTML/JavaScript, schema/migration SQLite,
requirements và hai workflow GitHub Actions. Đã đọc luồng tích hợp, chạy
kiểm thử hiện có, bổ sung hồi quy và kiểm tra dashboard trong Chromium.
Audit không phải chứng nhận không còn bug hoặc chứng nhận đúng ngữ nghĩa Hàn–Việt.

## 1. Lỗi nghiêm trọng và lỗi tích hợp

Không có lỗi P0 được xác nhận. Các lỗi P1/P2 dưới đây đã sửa trong mã nguồn.

| Mức | File | Vì sao lỗi / tình huống kích hoạt | Sửa |
|---|---|---|---|
| P1 | `studio/service.py`, `studio/scheduler.py`, `studio/database.py`, `studio/youtube.py`, `studio/instagram.py`, `studio/publisher.py` | Lịch đăng chỉ lưu nền tảng; đến giờ mới lấy tài khoản hiện tại. Đổi Page/OAuth/tài khoản Instagram sau khi đặt lịch có thể đăng nhầm nơi. Thumbnail YouTube cũng có rủi ro tương tự. | Lưu hash định danh tài khoản cùng job; kiểm tra khi dispatch và trên chính snapshot credential được API dùng. Không lưu secret trong SQLite. Migration giữ lịch cũ thiếu binding ở `needs_review`. |
| P1 | `studio/service.py`, `studio/ui.html` | UI bỏ đường dẫn thành basename; `video_path()` lấy thư mục khớp đầu tiên. Khi đổi thư mục xuất và hai nơi có cùng tên MP4, người dùng chọn B nhưng có thể đăng A. | Form gửi đường dẫn đầy đủ; chỉ nhận file trong thư mục xuất cho phép. Basename mơ hồ bị từ chối thay vì chọn ngẫu nhiên. |
| P1 | `studio/ai.py`, `studio/review.py`, `studio/seo.py`, `studio/answer_enrichment.py` | Provider có thể trả `content: null`, candidate rỗng hoặc envelope sai. TypeError/IndexError có thể thoát khỏi fallback, khiến provider kế tiếp không được thử. SEO JSON array cũng gây AttributeError. | Parser envelope chung trả `ProviderError`; xác thực object SEO; hồi quy kiểm tra provider thứ hai vẫn được gọi. |
| P2 | `studio/service.py` | Khi verify bị bận/lỗi/stale, `verify_pending_questions()` ghi `unavailable` vô điều kiện, có thể thay QA đã đạt hoặc QA mới hơn. | Khóa media cho toàn lượt; lỗi được báo về kết quả nhưng không ghi đè QA. Ghi thành công vẫn kiểm tra snapshot nội dung trong Store. |
| P2 | `studio/publisher.py` | Upload bằng file object không có Content-Length khiến urllib dùng chunked transfer. `file_size` là header riêng, không thay thế Content-Length HTTP. Điều này không bảo đảm tương thích hợp đồng upload raw MP4 của Meta. | Gửi Content-Length đúng kích thước file; kiểm thử request thực tế được tạo. Chưa khẳng định upload Meta thật đã được kiểm chứng. |
| P2 | `studio/production.py` | `convert('RGBA')` cấp phát ảnh trước khi từ chối logo quá kích thước. File nén nhỏ có thể giải mã rất lớn. Logo cũng từng được ghi trực tiếp vào file đang được preview đọc. | Kiểm tra kích thước trước convert; ghi PNG qua file tạm và atomic replace. |
| P2 | `studio/production.py` | Chỉ khóa lúc ghi, còn read–modify ở ngoài lock. Hai cập nhật khác trường chạy đồng thời có thể làm mất một thay đổi. | Khóa toàn bộ giao dịch read–validate–write. |
| P2 | `studio/youtube.py`, `studio/facebook_oauth.py`, `studio/local_storage.py` | Các file OAuth tạm dùng tên cố định, được tạo theo umask rồi mới chmod; concurrent write có thể tranh file tạm, và Linux có một khoảng ngắn file chưa là 0600. | Helper private JSON dùng mkstemp 0600 ngay từ lúc tạo, tên độc nhất, flush/fsync và atomic replace. Lỗi protect không làm mất file cũ. |
| P2 | `studio/autopilot.py`, `studio/models.py` | Plan có `levels/formats: null` hoặc số null gây TypeError; response model không phải object có thể vỡ lúc gọi `.get()`. | Kiểm tra kiểu dữ liệu và đưa ra lỗi validation có kiểm soát. |
| P2 | `studio/service.py`, `studio/cache_manager.py` | File có thể bị xóa/đổi trong lúc scan/stat; lỗi này có thể làm cả lượt status hoặc dọn cache thất bại. | Thu thập stat một lần, bỏ qua file đã biến mất; không liệt kê symlink ra ngoài thư mục xuất. |

Bốn hồi quy đầu tiên về basename, QA, logo và chọn đường dẫn đầy đủ đã được
chạy trước khi sửa: **3 failure + 1 error**. Sau sửa đều đạt.

## 2. Tối ưu đã áp dụng

- Renderer giữ tối đa hai RGB frame tĩnh cho mỗi câu (prompt và reveal), chỉ
  tái dùng sau fade-in và trước fade-out. Countdown/transition vẫn vẽ từng frame.
  Không cache toàn video; cache được giải phóng sau từng câu.
- Benchmark local cùng preset: một câu, 540x960, 24 FPS, think=4s,
  reveal=1.5s, transition=.3s, intro=.5s, không TTS/SFX. Baseline **3.090s /
  173 lần draw_frame**; cached **1.905s / 117 lần**. Giảm khoảng **38% thời gian**
  trong phép đo này. Đây là một mẫu local, không phải bảo đảm mọi máy/video.
- FFmpeg giải mã hai MP4 ra `framemd5`: **toàn bộ frame giống nhau**.
- `remembered_prompts()` gộp lấy đáp án vào một SQL query thay vì query bổ sung
  cho từng prompt; giữ nguyên chỉ mục và nguyên tắc chống trùng.
- Scan kho MP4 lấy stat một lần/file; bổ sung index lookup outbound theo video.

## 3. Refactor đã áp dụng

- `studio/local_storage.py`: dùng chung serialization/atomic write cho OAuth,
  bỏ phụ thuộc Facebook OAuth vào private helper của YouTube.
- `studio/publishing_accounts.py`: định nghĩa binding tài khoản tập trung;
  các publisher kiểm tra snapshot trước I/O thay vì chỉ tin trạng thái UI.
- `studio.ai.response_text()`: thống nhất kiểm tra response của generation,
  blind review, enrichment và SEO, tránh bốn bản parser khác nhau.
- Gộp form và danh sách lịch Facebook/YouTube/Instagram, giữ API cũ tương thích.
  Mỗi nền tảng có durable claim/outcome riêng; upload chạy tối đa ba thread.

Không thực hiện viết lại kiến trúc toàn bộ dashboard hoặc thay framework trong
bản sửa này. Những thay đổi đó không có bằng chứng là cần thiết để sửa lỗi trên.

## 4. Kiểm chứng và phát hành

- Python 3.12 trên Linux; dependencies không xung đột theo `pip check`.
- Suite đầy đủ local: **249 test, 248 đạt, 1 skipped (Windows DPAPI)** trong
  31.240s. Bao gồm các hồi quy mới trong `tests/test_repo_audit_v150.py` và
  `tests/test_multiplatform_oauth.py`. Kết quả Windows được xác định riêng bởi CI.
- Chromium headless: load dashboard không lỗi JavaScript; chọn cả ba nền tảng,
  gửi đúng đường dẫn và payload; trường Instagram/YouTube hiển thị đúng.
- Workflow Windows được nâng lên v1.5.0: chạy suite trên Python 3.11, build
  one-file GUI EXE chứa FFmpeg/FFprobe, khởi chạy EXE thật và kiểm tra HTTP,
  memory backup/merge, OAuth module và validation form đa nền tảng trước release.
- Tag release theo `v1.5.0-build.<run_number>`, gắn đúng commit và upload
  `TiengHanSoEasyStudio.exe`. Build hoặc publish chỉ được coi là thành công
  khi GitHub Actions và asset thực tế được xác nhận; sửa workflow không tự
  chứng minh EXE đã build xong.

## 5. Giới hạn và nâng cấp dữ liệu

1. Đóng EXE cũ; sao lưu toàn bộ `%LOCALAPPDATA%/TiengHanSoEasyStudio`, đặc biệt
   database và file cấu hình riêng tư. Không xóa bộ nhớ chống trùng để nâng cấp.
2. Lịch upload/thumbnail cũ thiếu account binding được chuyển `needs_review`,
   giữ nguyên nội dung/lịch sử. Kiểm tra trên nền tảng trước khi đăng/đặt lại,
   tránh tạo bài trùng. Không có tự retry upload không chắc chắn.
3. YouTube binding theo OAuth grant: đăng nhập lại có thể đổi grant dù cùng
   channel. Lịch cũ sẽ dừng an toàn thay vì đoán tài khoản. Không dùng nhiều
   worker/dashboard cùng database.
4. OAuth Facebook cần Meta App, quyền, redirect URI được Meta chấp nhận và
   App Review/Advanced Access theo trường hợp. Loopback callback không thay
   thế yêu cầu của Meta; Page token thủ công vẫn là lựa chọn dự phòng.
5. Không có tài khoản/token live được dùng trong audit. Chưa kiểm chứng OAuth,
   AI, KRDict, TTS online hay đăng thật lên Facebook/Instagram/YouTube. Offline
   mock và Windows smoke không thay thế các bước đó. TikTok vẫn là đăng thủ công.
6. Linux lưu OAuth dạng base64 với quyền file 0600; đây không phải mã hóa.
   Windows dùng DPAPI của tài khoản hiện tại; không chia sẻ các file credential.
7. Các kiểm tra từ điển/AI không bảo đảm mọi nghĩa và phiên âm đúng. Vẫn cần
   người duyệt nội dung trước khi phát hành công khai.
