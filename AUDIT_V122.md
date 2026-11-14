# Audit và bản sửa v1.2.2

Ngày: 14/11/2026 (giờ Việt Nam).

## Phạm vi và kết luận

Đã rà soát CLI/launcher Windows, toàn bộ module `studio/`, giao diện HTML/JavaScript,
SQLite, luồng AI/QA/KRDict, dựng hình/âm thanh/metadata/cache, tích hợp mạng xã hội,
tests và hai workflow CI. Thay đổi đã áp dụng trong checkout; chưa push hoặc phát hành EXE.

**Đã xác định nhiều lỗi tích hợp có thể gây triệu chứng KRDict trên Windows.**
Không thể khẳng định nguyên nhân duy nhất trên máy người dùng khi chưa có thông báo lỗi,
key thực tế hoặc nhật ký đã loại bỏ thông tin nhạy cảm. Không có key thật nào được sử dụng trong audit.

## 🔴 1. Lỗi nghiêm trọng và lỗi chức năng đã sửa

Mức Critical dùng cho nguy cơ mất dữ liệu hoặc gửi tác vụ trùng; High/Medium được phân biệt
để tránh gán mọi vấn đề thành lỗi Critical. Các đường dẫn dưới đây trỏ tới mã sau khi sửa.

| Mã / mức | Tệp | Vì sao lỗi xảy ra trước đây | Bản sửa |
|---|---|---|---|
| C01 / Critical | `studio/keys.py` | JSON hỏng/không đọc được bị đổi thành `{}`; lần nhập tiếp theo có thể ghi đè toàn bộ kho key cũ. | Báo lỗi đọc/giải mã rõ ràng, không ghi đè; khóa cả đọc/migration/ghi. |
| C02 / High | `studio/keys.py`, `studio/ui.html` | File TXT được đọc cố định bằng UTF-8; file UTF-16 do Windows/PowerShell tạo bị sai mã hóa. Key có dấu nháy bị kiểm tra như một phần của key; KRDict còn bị ép đúng 32 hex trước khi gọi API. | Nhận UTF-8 và UTF-16 LE/BE có BOM, bỏ cặp dấu nháy bao ngoài, thông báo file giải mã sai. Credential được coi là chuỗi do nhà cung cấp cấp; API thật quyết định tính hợp lệ. |
| C03 / High | `studio/krdict.py`, `studio/webapp.py` | Tra cache trước kiểm tra key; Test KRDict dùng cùng đường tra cache, nên key đã đổi/xóa vẫn có thể được báo thành công. | Test luôn gọi mạng trực tiếp; kiểm tra sự hiện diện key trước cache; đánh phiên bản cache để làm mới dữ liệu cũ. |
| C04 / High | `studio/krdict.py`, `studio/ui.html`, `studio/review.py` | HTML từ proxy, XML sai cấu trúc hoặc lỗi API bọc trong XML có thể bị coi là không tìm thấy từ. Lỗi HTTP/mạng thường bị gộp thành lỗi key/quota. Giao diện cũng khuyên kiểm tra key khi chỉ thiếu kết quả dịch. | Kiểm tra cấu trúc channel/total và lỗi API; phân biệt lỗi mạng, TLS, timeout, HTTP, thiếu key, phản hồi sai và thiếu nghĩa Việt. Thông báo được giữ trên giao diện và báo cáo QA, không chứa URL/key. |
| C05 / High | `studio/ui.html`, `studio/keys.py` | UI luôn append; KRDict chỉ sử dụng key đầu tiên. Nhập key mới không thay được key cũ hết hiệu lực ở đầu bộ. | Thêm tùy chọn **Thay bộ key của các dịch vụ có trong nội dung nhập**, giữ dịch vụ khác; mặc định vẫn append, tự bỏ chọn sau khi nhập thành công. Không xoay key để vượt quota. |
| C06 / High | `studio/keys.py`, `launcher.py`, `studio/webapp.py` | Lỗi DPAPI dùng thông báo Page token dù đây là kho API key. Launcher mới có thể mở dashboard của EXE cũ vì health check chỉ nhận diện tên ứng dụng. | Thông báo đúng tài khoản Windows/kho key; health có version và launcher yêu cầu đúng version; hướng dẫn đóng EXE cũ khi cổng đang bị chiếm. |
| C07 / High | `studio/krdict.py`, `studio/review.py`, `studio/quiz.py` | Bỏ dấu biến cá/cà, đắt/đặt thành cùng từ; validator chỉ cần một trong bốn lựa chọn có Hangul; strict QA vẫn có thể duyệt khi thiếu bằng chứng về đáp án nhiễu. | Giữ dấu và chuẩn hóa Unicode NFC; hỗ trợ từ một ký tự như “ô”; cả bốn lựa chọn phải có Hangul; strict giữ câu ở trạng thái cần duyệt nếu chưa đủ bằng chứng về đáp án nhiễu. Không cắt bỏ mục từ đồng âm sau 5 kết quả. |
| C08 / High | `studio/exports.py` | Queue thủ công chỉ nhận `approved`, trong khi renderer và UI hỗ trợ cả `auto_approved`. | Đồng bộ hai trạng thái hợp lệ; có regression test chạy worker thật với renderer giả lập. |
| C09 / Critical | `studio/database.py` | Khóa Python chỉ bảo vệ một Store. Hai Store/process có thể cùng SELECT lịch đang chờ trước khi UPDATE và cùng nhận một job. | Transaction `BEGIN IMMEDIATE` đặt quyền ghi trước bước đọc/sửa cho các thao tác ghi, gồm Page, outbound và thumbnail claims. |
| C10 / High | `studio/publisher.py`, `studio/scheduler.py` | Upload Meta trả `{}` vẫn đi tiếp vì chỉ kiểm tra `success is False`. Lỗi enqueue thumbnail có thể đổi trạng thái một upload YouTube đã xác nhận sang lỗi upload. | Bắt buộc `success is True` trước finalize; tách enqueue thumbnail khỏi khối đánh dấu kết quả upload. |
| C11 / High | `studio/youtube.py` | Chỉ xin `youtube.upload` nhưng gọi `videos.list(part=processingDetails)`, API này cần scope đọc. Thumbnail chờ video xử lý có thể thất bại dù upload thành công. | Xin thêm `youtube.readonly`; token cũ thiếu scope được yêu cầu kết nối lại, không giả định refresh sẽ tự cấp thêm quyền. |
| C12 / High | `studio/service.py`, `studio/webapp.py`, `studio/renderer.py` | Synchronous render/preview/TTS không luôn hiện trong export queue, nên cleanup hoặc sửa metadata có thể chạy cùng lúc. FFmpeg ghi trực tiếp file đích khiến MP4 lỗi có thể xuất hiện trong danh sách. | Khóa chung cho render/preview/TTS/cleanup/metadata/thumbnail/logo; mux vào staging cùng filesystem, chỉ rename file hoàn tất ra đích. |
| C13 / Medium | `studio/review.py`, `studio/answer_enrichment.py`, `studio/seo.py`, `studio/voice.py` | Catch RuntimeError rộng nuốt cả BudgetExpired/RunCancelled, dẫn tới thử thêm provider sau yêu cầu dừng. Edge TTS chưa có giới hạn toàn thao tác save. | Truyền tiếp các tín hiệu deadline/cancel; giới hạn Edge save bằng `asyncio.wait_for`, vẫn giữ xác minh TLS. |
| C14 / Medium | `studio/database.py`, `studio/service.py` | Chọn câu cần QA từ 100 câu mới nhất có thể bỏ sót hoàn toàn câu cũ. Retry thumbnail lấy attempt từ 80 dòng giao diện nên có thể đọc sai job cũ; cutoff cũng cho thêm một lượt. | Truy vấn trực tiếp danh sách pending trong SQL; lấy attempts theo video_id trong transaction, dừng sau 8 lượt. |
| C15 / Medium | `studio/exports.py`, `studio/cache_manager.py`, `studio/webapp.py` | Autopilot không dọn history; metadata thời gian không được dọn theo job; elapsed tiếp tục tăng sau khi xong. File cache vừa rename có thể làm stats lỗi. Tên MP4 Unicode gây lỗi header Latin-1 khi tải. | Giới hạn 30 job cùng toàn bộ metadata; đóng băng thời gian hoàn tất; bỏ qua file cache vừa biến mất; dùng Content-Disposition UTF-8. |
| C16 / High | `.github/workflows/build-windows.yml`, `studio/__init__.py`, `main.py` | Workflow thay asset trên cùng tag cố định làm EXE mới có thể không khớp commit của tag. Runtime version nằm ở nhiều nơi; CLI worker không phục hồi trạng thái upload bị gián đoạn. | Bump v1.2.2; tag release thêm build number, target đúng github.sha; health lấy version từ package; worker phục hồi lịch bị gián đoạn thành trạng thái cần kiểm tra. |

## 🟡 2. Tối ưu đã áp dụng

| Thay đổi | Hiệu quả và bằng chứng |
|---|---|
| JOIN bảng QA khi liệt kê câu hỏi | Kiểm tra cùng fixture 500 câu: bản HEAD cũ chạy **501 SELECT**, bản sửa chạy **1 SELECT**, trả đủ 500 câu. Một lượt đo cục bộ: 0,0243s → 0,0200s; đây không phải benchmark hiệu năng production. |
| Vẽ intro một lần rồi tái sử dụng bytes | Từ khoảng 45 lần vẽ ở cấu hình intro 1,5s/30fps xuống 1 lần/video. Regression test kiểm tra một lần gọi kể cả khi mux thất bại. |
| Cache logo đã decode/resize | Cache giới hạn 8 biến thể, khóa theo đường dẫn/mtime_ns/kích thước file và kích thước hiển thị; không mở và resize cùng logo mỗi frame. |
| Đọc kho key một lần cho status | Một snapshot dùng chung cho bốn provider; giảm đọc file và giải mã DPAPI lặp trong mỗi lần polling. |
| Dừng sớm khi KRDict không khả dụng | Sau lỗi kết nối/API chung hoặc thiếu key, không gọi lại cùng dịch vụ cho ba đáp án còn lại của câu đó. Regression test xác nhận chỉ gọi một lần và giữ chẩn đoán. |
| Giới hạn history và truy vấn đúng đối tượng | Không tăng các map job/timing vô hạn; tìm pending QA/attempt bằng SQL thay vì quét trang dữ liệu giao diện. |
| Thiết lập WAL tại init | Không đặt lại journal mode ở mỗi connection; vẫn đóng mọi connection và bảo vệ transaction ghi. |

Không bật song song hàng loạt request AI/KRDict: điều đó có thể tăng quota/timeouts và
không giải quyết nguyên nhân key cũ hoặc lỗi quyền truy cập.

## 🟢 3. Refactor đã áp dụng

- `KRDictError` cung cấp mã chẩn đoán an toàn thay cho suy đoán từ một chuỗi lỗi chung.
- `KRDict.test_connection()` tách xác thực trực tiếp khỏi tra từ có cache.
- `_merge_keys()` dùng chung quy tắc gộp key cho get/count/readiness; thao tác đọc và migration có cùng khóa.
- `Store.db(immediate=...)` là transaction context manager rõ ràng; pending QA và retry theo ID nằm tại lớp storage.
- `media_operation` dùng chung cơ chế bảo vệ các thao tác media của Service.
- `ExportQueue._finish()` tập trung dọn history và metadata cho cả manual và Autopilot.
- Version runtime dùng chung từ package; bổ sung KRDICT_API_KEY/KRDICT_API_KEYS trong `.env.example`.
- CI Ubuntu cài rõ FFmpeg và font Noto CJK/DejaVu để kiểm thử render không bị bỏ qua vì thiếu thành phần.

## 🛠️ 4. Áp dụng, kiểm thử và giới hạn

### Bằng chứng tái hiện với mã HEAD trước bản sửa

Audit tải module cũ từ `git show HEAD:studio/...` trong bộ nhớ, dùng SQLite tạm và key giả:

- Cache còn từ 물 + không có key: mã cũ trả `exact=true`; bản sửa trả `key_missing`.
- `<html>proxy</html>`: mã cũ trả `exact=false`; bản sửa trả lỗi `invalid_response`.
- Key 32 ký tự có cặp dấu nháy: parser cũ từ chối; parser mới nhận chuỗi key sau khi bỏ dấu nháy.
- 500 câu hỏi: 501 SELECT → 1 SELECT như bảng tối ưu.

Các schema OAuth được đối chiếu với discovery document YouTube v3 đi kèm
`google-api-python-client`: videos.list không chấp nhận riêng scope youtube.upload,
trong khi videos.insert và thumbnails.set chấp nhận scope đó.

### Kiểm thử

Lệnh dùng từ thư mục repository:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m pip --no-cache-dir check
git diff --check
```

Kết quả cuối được ghi ở phần cập nhật kiểm chứng bên dưới. Các bài mới kiểm tra:
key giả/encoding/DPAPI lỗi/cache/error classification, HTTP import/test/download,
QA/deadline/cancel, worker queue, SQLite nhiều Store đồng thời, staging với FFmpeg thật,
OAuth scope/thumbnail retry và xác nhận upload Meta. Mạng xã hội và API từ điển dùng mock,
không có bài đăng thật hoặc key thật.

### Đưa bản sửa lên Windows

1. Sao lưu `%LOCALAPPDATA%\TiengHanSoEasyStudio` trước khi thay EXE.
2. Build từ mã đã sửa bằng workflow **Build Windows EXE**. Artifact vẫn mang tên
   `TiengHanSoEasyStudio-Windows-EXE`; release từ main có tag `v1.2.2-build.<run_number>`.
3. Đóng hoàn toàn EXE cũ rồi mở EXE mới; kiểm tra dashboard hiển thị v1.2.2.
4. Nếu trước đây đã nhập nhiều key KRDict: chọn KRDict, bật **Thay bộ key**, chỉ nhập
   key hiện còn hiệu lực rồi bấm **Test KRDict**. Không bật thay cho file kết hợp nhiều
   dịch vụ nếu bạn muốn giữ những bộ key có trong file đó.
5. Nếu dùng YouTube đã kết nối từ bản cũ, kết nối lại để đồng ý thêm quyền đọc phục vụ
   kiểm tra trạng thái xử lý video.

### Các giới hạn cần ghi nhận

- Audit chạy trên Linux/Python 3.12. Chưa build/chạy Windows EXE mới; kiểm thử DPAPI thật
  được thêm vào suite Windows, nhưng không thể chạy trên Linux.
- Chưa xác minh key thật, TLS/proxy/tường lửa trên máy Windows của người dùng hay API KRDict live.
  Lỗi tài khoản/quota/key bị thu hồi phải được xác nhận với nhà cung cấp.
- File DPAPI thuộc tài khoản Windows khác không thể được giải mã bằng bản sửa; cần đúng
  tài khoản gốc hoặc nhập lại key sau khi sao lưu. Bản sửa không tự xóa file đó.
- QA từ điển và AI vẫn là bằng chứng hỗ trợ, không chứng minh mọi nghĩa theo ngữ cảnh.
  Strict mode mới bảo thủ hơn và có thể giữ nhiều câu ở trạng thái cần duyệt.
- Cancel/deadline là hợp tác; FFmpeg đang dựng video được phép hoàn tất, không có cam kết
  hard real-time. Khóa media bảo vệ trong một Service/process; không hỗ trợ chạy nhiều
  ứng dụng cùng ghi media vào một thư mục.
- Chưa có push, tag, release hay thay EXE trên máy người dùng. Workflow đã sửa chỉ chạy
  khi mã được đưa lên GitHub; file EXE đã tải trước đây không tự thay đổi.

## Cập nhật kiểm chứng cuối

- Suite đầy đủ: **133 bài — 132 đạt, 0 lỗi, 1 bỏ qua** trong 20,498 giây.
- Bài bỏ qua: `test_real_windows_dpapi_unicode_roundtrip`, cần Windows thật.
- Thêm **30 bài hồi quy** trong `tests/test_audit_regressions.py`; hai test cũ được
  cập nhật đúng điểm mock/kiểu lỗi sau refactor, không bỏ assertion hoặc tắt kiểm thử.
- `pip check`: không phát hiện dependency hỏng. `git diff --check`: đạt.
- Hai workflow YAML parse thành công; workflow Windows chưa được thực thi tại đây.
- Server CLI v1.2.2 đã khởi động lại; GET `/health`, `/`, `/api/status`,
  `/api/topic/random` đạt; POST `/api/krdict/test` khi chưa có key trả HTTP 400 và
  mã `key_missing` đúng như dự kiến.
- Smoke test đóng gói Windows đã bổ sung kiểm tra version và chẩn đoán thiếu key;
  cần GitHub Actions chạy trên Windows để xác nhận EXE thực tế.
