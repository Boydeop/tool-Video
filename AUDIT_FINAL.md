# TIẾNG HÀN SO EASY STUDIO | AUDIT & FINAL RELEASE v1.0.0

**Phạm vi:** toàn bộ repo Boydeop/tool-Video từ v0.4.2 lên v1.0.0.  
**Chuẩn bằng chứng:** review source code và unit/CI; test mock không thể chứng minh AI dịch đúng hoặc nền tảng đã cấp phép đăng thật.

## Tóm tắt điều hành

v0.4.2 có SQLite, bộ dựng video dọc, TTS và lịch Facebook. Nhưng chưa đạt trạng thái sản xuất hàng loạt không cần tick, phục hồi sau mất điện, làm sạch cache và đăng lên nhiều nền tảng được ủy quyền. v1.0.0 bổ sung engine local-first có giới hạn/quyền riêng cho các bên.

## Risk register

| ID | Severity | Hiện trạng trước nâng cấp | Giải pháp/giới hạn mới |
|---|---|---|---|
| A01 | HIGH | Chỉ render câu đã tick | Autopilot 1–12 video/lượt, mỗi MP4 một topic mới, mỗi video 1–6 câu |
| A02 | HIGH | RAM-only export progress | production_runs SQLite, kết quả theo topic, trạng thái interrupted khi restart |
| A03 | HIGH | AI thiếu nghĩa/phiên âm nhưng có thể được duyệt | strict machine gate bốn đáp án, status auto_approved tách biệt human approved |
| A04 | HIGH | Chỉ lên lịch Facebook | YouTube OAuth + videos.insert, Instagram Professional Graph API với asset HTTPS; Facebook Page vẫn giữ |
| A05 | HIGH | Key pool ở JSON rõ trên Windows | DPAPI per-user, migration in place từ dữ liệu cũ; Unix chmod 0600 |
| A06 | MEDIUM | Tên file random, khó sắp thứ tự | THSE_YYYYMMDD_NNNNNN_topic_TOPIKn_1080x1920.mp4, seq SQLite |
| A07 | MEDIUM | Không quản lý cache TTS & preview | Xem dung lượng, xóa có whitelist + guard khi render |
| A08 | MEDIUM | Nội dung 9:16 sát vị trí overlay nút Shorts | 1080×1920 /30fps, shorts_safe inset khỏi mép phải và đáy |
| A09 | MEDIUM | MP4 không bật voice/SFX có thể thiếu AAC | AAC 48kHz silence track, H.264 yuv420p, faststart |
| A10 | MEDIUM | Scheduling 1 platform, rủi ro submit trùng | outbound_posts platform-specific, UNIQUE(video,platform), needs_review khi upload chưa rõ kết quả |
| A11 | MEDIUM | Thiếu phân biệt API read vs publish | Status OAuth connected vs publishing permission; YouTube private mặc định |
| A12 | MEDIUM | Dừng app làm lịch không chạy | Lịch bền vững SQLite nhưng cần app hoặc worker đang hoạt động |
| A13 | LOW | Metadata bị hiểu nhầm là DRM | Title/artist/copyright/comment chỉ nhận diện nguồn, không chống sao chép |
| A14 | HIGH | TikTok API Direct Post chưa được audit | Không lách hạn chế bằng browser automation; chuẩn bị MP4/caption đăng thủ công |
| A15 | MEDIUM | Ngân hàng 500 rows tối đa trên UI | Autopilot dùng DB trực tiếp; UI pagination server-side là công việc mở khi dữ liệu rất lớn |
| A16 | MEDIUM | Unlimited AI production dễ lạm dụng quota | cap 12 video/run, giữ cooldown API; không bảo đảm nội dung không trùng 100% |

## Kiến trúc mới

1. Topic rotation dựa trên thống kê theo tần suất, không lặp topic trong một lượt.
2. AI generation → kiểm tra độ dài, đáp án duy nhất, đủ tám thông tin nghĩa/phiên âm → draft hoặc auto_approved.
3. Một worker tạo MP4 với progress theo giọng đọc, frame, mux và metadata; đăng ký hoàn thành vào SQLite, đặt tên có sequence.
4. H.264 yuv420p 9:16 1080×1920 30fps; AAC 48k; TTS câu hỏi trước đếm ngược; logo kéo thả; shorts-safe.
5. Lịch Facebook Page từ bản trước; YouTube qua OAuth upload; Instagram qua Graph API cần URL HTTPS MP4.
6. Outbound từng nền tảng độc lập và idempotent; lỗi mạng sau upload được ghi needs_review, không tự đăng lại.
7. Cache whitelist gồm hashed TTS MP3 và preview_*.mp4; cấm xóa file MP4 thành phẩm, key, token và logo.

## Điều kiện thực tế cho đa nền tảng

| Nền tảng | Hình thức | Điều kiện |
|---|---|---|
| Facebook Page Reels | Meta Page Video Reels API | Page token, pages_manage_posts và quyền Page phù hợp |
| YouTube Shorts | YouTube Data API videos.insert | OAuth Desktop Google, youtube.upload scope, quota; video dọc được nhận diện Shorts bởi nền tảng |
| Instagram Reels | Graph API media / container / media_publish | Instagram Professional, quyền Meta, URL MP4 HTTPS công khai |
| TikTok | MP4 + caption thủ công | Direct Post public yêu cầu audit + creator consent; không hỗ trợ đăng công khai bằng công cụ chưa được cấp phép |

Chỉ kết nối đọc được Page/IG không chứng minh quyền đăng; phải chạy thử thật. Lịch local yêu cầu EXE hoặc worker đang chạy vào thời điểm phát hành.

## Release gate

- [ ] Unit tests Linux bao gồm tên file, autosafe, cache whitelist, SQLite run recovery và giả lập upload.
- [ ] Windows tests + EXE PyInstaller --windowed + HTTP smoke.
- [ ] FFprobe kiểm tra MP4 1080×1920 H264/AAC và metadata.
- [ ] Tự tạo 3 chủ đề và xác minh topic không trùng trong lượt, không render câu thiếu nghĩa.
- [ ] Check logout/restart: production run interrupted, không xóa video đã hoàn thành.
- [ ] Test live OAuth YouTube với tài khoản người dùng (không thể mock thay).
- [ ] Test live Meta Page/Instagram với quyền và public HTTPS URL hợp lệ (không thể mock thay).
- [ ] Giáo viên kiểm tra 10–20 quiz ngẫu nhiên về nghĩa/phiên âm.

## Rủi ro còn lại

**Chính xác tiếng Hàn:** QA tự động chỉ kiểm tra cấu trúc, không phải một từ điển có bằng chứng. Chưa nên phát hành hàng trăm video công khai mà không lấy mẫu kiểm duyệt.  
**Bản quyền:** metadata không phải Content ID/DRM hay tuyên bố sở hữu pháp lý.  
**TikTok:** hướng dẫn nội dung cấm watermark/promotional branding trên nội dung chia sẻ qua API; với app chỉ phục vụ một nhóm/tài khoản nội bộ, quy trình Direct Post có thể không phù hợp.  
**Instagram hosting:** Studio không cung cấp CDN hay tự đổi file C:\\ sang HTTPS.  
**Scale:** một máy, SQLite WAL, một render worker, không phải hệ thống server đa máy.  
**Tính sẵn sàng:** lên lịch tự động chỉ hoạt động khi Studio/worker chạy và token hợp lệ.

### Official references
- https://developers.google.com/youtube/data/guides/uploading_a_video
- https://developers.google.com/youtube/data/guides/authentication
- https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api
- https://developers.facebook.com/docs/graph-api/reference/page/video_reels
- https://developers.tiktok.com/docs/en/content-posting-api-reference-direct-post
- https://developers.tiktok.com/docs/en/content-sharing-guidelines

## Kết luận

v1.0.0 cải thiện kiến trúc local-first và khả năng tự dựng nhiều video; phát hành ổn định phụ thuộc CI qua tất cả cổng. Tích hợp đăng live của từng tài khoản vẫn phải được xác thực ngoài CI; không quảng cáo là public TikTok autopublisher hay AI dịch chính xác tuyệt đối.


## v1.1.0: Audit supplement based on actual user screenshots

| ID | Severity | Confirmed v1.0.0 root cause | Corrective change |
|---|---|---|---|
| B01 | HIGH | Entire batch rejected if fewer than 5/5 questions pass; AI output often omits one option meaning or romanization; clue/meaning string comparison penalizes valid grammar | Structural gate without false clue comparison, AI enrichment and bounded retry, optional curated vocabulary fallback, partial shorter MP4 |
| B02 | HIGH | Shorts Safe resizes the whole design to 421x749 at (20,63), causing large blank areas | Balanced 462x821 inset, full-canvas procedurally drawn Korean cultural motifs |
| B03 | MEDIUM | Copyright form always visible and requires user to interpret fields | Automatic metadata by default with optional manual override in Settings, SEO title sync |
| B04 | MEDIUM | Separate manual generation and manual batch-render controls duplicate Autopilot | Five-tab navigation with less-used manual functions collapsed in Advanced |
| B05 | MEDIUM | No upcoming holiday topic calendar | Separate Gregorian 55-day event mode in VN timezone, events API and event-specific subtopic plan |
| B06 | MEDIUM | UI production payload hardcodes fps=24 even for 1080p | Resolution-based 30fps/24fps, additional regression coverage |
| B07 | LOW | Plain backgrounds and little cultural character | Subtle Hanok, mountain, Taegeuk, flower and lattice illustrations via PIL |

Residual risks: AI structural checks cannot certify Korean linguistics, seasonal curated vocabulary is limited and needs review, lunar festival dates require authoritative year-specific calendars, public publishing still depends on Meta/Google account rights, and scheduled posting needs Studio running.
