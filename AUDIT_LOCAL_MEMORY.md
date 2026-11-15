# Audit và nâng cấp bộ nhớ local — 15/11/2026

Baseline: `7797c00ce9089320b1bf3c583049dfa5e322733b`.
Các thay đổi được chuẩn bị cho bản v1.4.1; kết quả phát hành Windows được
ghi trong GitHub Actions/Release tương ứng. Bản GitHub v1.4.0 build 42 trước đó
chưa chứa các sửa này.

Phạm vi: storage/migration, sinh và sửa câu, QA KRDict/AI, hàng đợi và recovery,
render/TTS/metadata/thumbnail, HTTP/UI, thông tin xác thực, lịch đăng đa nền tảng,
CLI, dependencies và workflow build Windows. Kết hợp đọc mã, kiểm thử hiện có,
kiểm thử hồi quy mới và đo hiệu năng. Không dùng khóa thật hay đăng nội dung
lên nền tảng. Audit này không phải chứng nhận không còn lỗi.

## 🔴 Lỗi logic và tích hợp đã sửa

Không xác nhận được lỗi mức P0/critical theo nghĩa mất toàn bộ hệ thống. Các
lỗi tác động cao bên dưới được phân loại P1; vấn đề độ tin cậy được ghi P2.

| Mức | Vị trí | Vì sao lỗi xảy ra / tác động | Sửa đã áp dụng |
| --- | --- | --- | --- |
| P1 | `studio/batch_store.py`: trash/purge/discard; `studio/database.py`: insert/apply_repair | Trash thay hash, purge/retry xóa hàng, sửa đáp án thay hash cũ. Chống trùng phụ thuộc ngân hàng hiện tại nên quên nội dung và đáp án cũ. | Sổ append-only `question_memory`; backfill câu/thùng rác/revisions; ghi sổ cùng transaction với câu; sửa, khôi phục và sinh câu đều kiểm tra sổ. |
| P1 | `studio/database.py`, `studio/quiz.py` | Chỉ so `question`, không so câu gợi ý thực tế trên video. Khác khoảng trắng/ký tự ẩn có thể lách so đáp án và tạo lựa chọn trông giống nhau. | Chuẩn hóa Unicode, ký tự định dạng, dấu câu; thêm alias `short_clue`; so đáp án bỏ khoảng trắng; chặn lựa chọn trùng và câu chỉ có dấu câu. |
| P1 | `studio/service.py`, `studio/database.py` | QA/enrichment đọc bản cũ rồi ghi sau khi người dùng sửa. Render cũng có thể ghi nhận câu đã sửa, duyệt lại hoặc đưa vào trash trong lúc encode. | Khóa media cho thao tác nội dung và đối chiếu snapshot trong transaction trước khi ghi QA/enrichment/registry. Chặn ID lặp và câu trong trash. |
| P1 | `studio/review.py` | QA đối chiếu nghĩa KRDict với clue và AI chọn đáp án, nhưng không đối chiếu nghĩa Việt được hiển thị dưới từng lựa chọn. Có thể duyệt video có bản dịch sai. | Đối chiếu cả bốn nghĩa; strict dừng trước gọi AI nếu thiếu khớp; assisted cũng không bỏ qua mâu thuẫn có bằng chứng. QA v4 và migration hủy các auto-approval cũ một lần, giữ duyệt tay/video. |
| P1 | `studio/webapp.py` | Chỉ kiểm tra Origin cho POST; GET bỏ qua Host/Origin. Bind loopback không tự chống DNS rebinding: trang web độc hại có thể đọc dữ liệu local qua hostname của nó trỏ về loopback. | Xác thực Host đúng localhost/127.0.0.1 và cổng thực trên GET/POST; chặn Origin ngoài; POST chỉ nhận JSON, từ chối Transfer-Encoding. Kiểm thử boundary bằng HTTP trực tiếp, không tuyên bố đã mô phỏng mọi trình duyệt. |
| P1 | `studio/service.py`: render_approved | MP4 đã ở thư mục xuất trước khi ghi metadata/registry. Nếu metadata/callback/commit lỗi, MP4 chưa hoàn tất vẫn có thể xuất hiện như video legacy. | Dọn file do lượt render đó tạo khi thất bại; không ghi registry/trạng thái rendered nếu snapshot không còn đúng. Test chèn lỗi metadata và callback muộn. |
| P2 | `studio/renderer.py` | Parent ghi raw frames vào stdin, chỉ đọc stderr sau khi ghi xong. Encoder ghi lỗi đủ đầy pipe stderr sẽ chặn, trong khi parent cũng chờ ghi stdin. | Spool stderr ra file tạm; giới hạn thời gian chờ encoder sau khi gửi frames; kill/reap trước đóng stdin khi lỗi. Test encoder giả ghi 1 MB stderr trong subprocess có timeout. Đây không phải hard timeout cho mọi thao tác ghi frame/driver treo. |
| P2 | `studio/ai.py`, `studio/voice.py` | Sinh thủ công vẫn thử provider chưa cấu hình; thiếu key có thể bị coi là lỗi có thể thử lại. gTTS dùng timeout cố định thay vì ngân sách tác vụ. | Lọc provider khả dụng cho cả manual/autopilot; thiếu provider báo blocked; gTTS dùng timeout còn lại. Deadline vẫn mang tính cooperative, không phải kill switch OS. |

## 🟡 Tối ưu đã áp dụng

- Chống trùng chính xác dùng chỉ mục `(kind,digest)`. Gần trùng dùng khoảng độ
  dài có chỉ mục, được suy ra từ cận trên của SequenceMatcher; chỉ chạy alignment
  khi `quick_ratio` có thể đạt ngưỡng. Không cắt lịch sử để đổi lấy tốc độ.
  Trường hợp xấu nhất nhiều nội dung cùng độ dài vẫn cần quét nhiều ứng viên.
- Cache LRU có giới hạn cho nền ở độ phân giải đầu ra, đo chiều dài chữ và phân
  đoạn Việt/Hàn; `.copy()` nền trước vẽ để tránh làm bẩn cache. Giữ native Hangul.
- Ngữ cảnh nhắc AI lấy cả lịch sử đã xóa từ sổ nhớ. 50 mục chỉ là gợi ý prompt;
  kiểm tra nhận câu luôn dùng toàn sổ.
- Backup/import xử lý từng dòng JSONL; import atomic và idempotent. Backup không
  nạp toàn bộ sổ vào RAM; mỗi dòng import có giới hạn và không tin hash do file cung cấp.

Đo so sánh bằng mã renderer baseline lấy trực tiếp từ Git và mã mới, cùng máy
Linux/Pillow 12.3.0, 32 khung 1080p với cache đã warm: **3,662 s → 2,437 s**,
tương đương **1,50×** cho phần vẽ khung. So sánh **48 khung** gồm ba theme, hai
độ phân giải và nhiều giai đoạn: byte RGB khớp hoàn toàn. Không suy diễn mức
tăng này cho toàn bộ thời gian TTS/network/encode hoặc cho mọi máy Windows.

## 🟢 Refactor đã áp dụng

- Tách canonicalization, migration, kiểm tra trùng, thống kê và backup sang
  `studio/memory.py`, tránh tiếp tục nhồi logic vào ngân hàng câu hỏi.
- Dùng chung `_check_memory` cho tạo/sửa/khôi phục và `_remember` cho migration/
  lịch sử mới, tránh ba chính sách chống trùng khác nhau.
- Dùng đối số snapshot `expected` ở persistence thay vì chỉ tin khóa UI.
- Tập trung kiểm tra Host/Origin trong một guard dùng chung cho GET/POST.
- Thêm CLI/API và giao diện bộ nhớ để người dùng quan sát, sao lưu và gộp;
  trả số ứng viên trùng trong kết quả generation để hỗ trợ chẩn đoán.

## Bộ nhớ local: cam kết và giới hạn

`question_memory` nằm cùng `studio.sqlite3`, có trigger cấm UPDATE/DELETE qua
SQL thông thường. Không có nút reset; trash/purge/cache không xóa sổ. Chỉ đáp án
đúng bị giữ độc quyền, đáp án nhiễu được tái sử dụng. Quy tắc áp dụng toàn chủ
đề/cấp độ. Lưu cả nội dung từ các lượt sản xuất lỗi đã được nhận vào DB.

Chặn exact/canonical và gần trùng với ngưỡng prompt 0,89, đáp án dài 0,94.
Đây không phải thuật toán chứng minh không trùng nghĩa. Ví dụ hai câu diễn đạt
khác dùng hai từ Hàn đồng nghĩa có thể vượt qua. Sổ không thể sinh vô hạn câu
độc nhất từ tập từ vựng hữu hạn; khi thiếu câu mới ứng dụng trả thiếu thay vì
tái dùng nội dung cũ. Sửa lại cùng một câu đang tồn tại được phép, nhưng không
giải phóng những đáp án nó từng giữ cho câu mới.

Lịch sử đã bị xóa trước lần nâng cấp không tự phục hồi được. Mất DB và mọi
backup cũng làm mất trí nhớ; trigger không bảo vệ trước việc người dùng xóa
file SQLite hoặc quản trị viên chủ động sửa schema. Giữ thư mục dữ liệu khi
cài lại và sao lưu JSONL. Import chỉ gộp lịch sử, không khôi phục video/câu hỏi.

## Kiểm chứng

- Baseline: 192 test chạy, 191 đạt, 1 bỏ qua vì cần Windows DPAPI thật.
- Bộ mới: `python -m unittest discover -s tests -v` chạy **214 test trong
  41,912 giây: 213 đạt, 1 bỏ qua (Windows DPAPI)**. Bao gồm render MP4/audio thật, metadata readback,
  HTTP, queue 100 video với fault injection, migration, concurrency, backup
  rollback, retention qua purge/retry/repair và noisy encoder.
- Test cũ mong trash giải phóng chống trùng được đổi sang chính sách mới: vẫn
  chặn sinh lại nhưng cho phép khôi phục đúng câu gốc. Fixture load test trước
  đó dùng một clue cho hàng trăm câu; đã tạo dữ liệu phân biệt thực sự. Fixture
  KRDict đổi từ ghép sang danh từ đơn cũng được sửa nghĩa tương ứng; không tắt
  assertions để hợp thức hóa kết quả.
- Kiểm tra cú pháp JavaScript của UI nằm trong suite; `git diff --check` đạt.
- Smoke bổ sung: seed một câu → purge ngân hàng → CLI export/import sang data
  folder mới → CLI status → khởi động tiến trình `serve` thật. `/health` đạt,
  `/api/status` xác nhận ngân hàng 0 câu nhưng bộ nhớ vẫn giữ 1 câu/1 đáp án.
- Chuẩn bị release v1.4.1: sửa sai số đồng hồ Windows và bỏ sàn timeout 0,25 s
  khi ngân sách còn ít hơn; thêm regression cho cả hai trường hợp và hết hạn
  giữa hai lần đọc đồng hồ. Linux: 215 test trong 40,096 s, 214 đạt và 1 bỏ qua
  Windows DPAPI. Workflow Windows thêm smoke backup/merge trên EXE đóng gói.
- Không kiểm chứng lại Windows EXE, DPAPI thật, TTS online, khóa AI/KRDict,
  OAuth hoặc đăng thật Meta/YouTube/Instagram. Những luồng này cần môi trường
  Windows/quyền dịch vụ tương ứng. CI Windows hiện có vẫn build và smoke-test
  EXE; chỉ một lần CI mới thành công mới xác nhận được binary chứa thay đổi.
