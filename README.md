# 🇰🇷 Tiếng Hàn So Easy Video Studio

AI video quiz automation for Vietnamese learners of Korean, branded for https://tienghansoeasy.io.vn/.

**Status: MVP, requires production Facebook/API validation.** The GitHub repository is intentionally developed on a feature branch for review. This is not a cloud service.

## Features

- Free-tier-capable AI providers: Gemini, Groq, OpenRouter. Optional local Ollama.
- Provider fallback without bypassing quotas or rate limits.
- SQLite history with normalized hashes, exact and approximate duplicate checks.
- More than 50 topic families, day-by-day topic rotation and TOPIK levels.
- Draft -> human review -> approved -> render -> schedule -> publish.
- Vertical Korean quiz videos: Hanji, Blue Modern and Sejong templates.
- Facebook Page Reels publication using official Meta Graph API stages.
- Local dashboard at 127.0.0.1:8765 and Python CLI.

## Install (Windows or Linux)

Prerequisites: Python 3.10+, FFmpeg on PATH.

    python -m pip install -r requirements.txt
    python main.py serve

Open http://127.0.0.1:8765 in your browser. This unauthenticated local UI must **never** be exposed to a public network.

## API credentials

View .env.example for supported environment variables. The app reads OS environment variables. It does NOT automatically load the .env file.

PowerShell example:

    $env:GEMINI_API_KEY="your_own_key"
    $env:GROQ_API_KEY="your_own_key"
    $env:OPENROUTER_API_KEY="your_own_key"
    python main.py serve

Provider order: Gemini, Groq, OpenRouter, then local Ollama (opt-in ENABLE_OLLAMA=1). Default model IDs are examples and availability can change. Free tier limits depend on provider and account. Never commit real API keys or attempt quota circumvention.

## CLI workflow

Generate six drafts:

    python main.py generate --topic "Giao tiếp" --level "TOPIK 1" --count 6

Create one daily batch (drafts only):

    python main.py daily --count 6

View and approve after checking grammar, ambiguity and answers:

    python main.py questions
    python main.py approve QUESTION_ID_1 QUESTION_ID_2

Render approved questions:

    python main.py render QUESTION_ID_1 QUESTION_ID_2 --theme hanji

Schedule a video with an explicit timezone:

    python main.py schedule --video "FULL_PATH_TO_DATA_VIDEOS_MP4" --caption "Học tiếng Hàn" --at "2026-12-01T18:00:00+07:00"

Keep ONE process running continuously to publish scheduled jobs:

    python main.py worker

Or run the dashboard, which includes a worker:

    python main.py serve

Do not run worker and dashboard concurrently against the same job database. Daily batch creation can be triggered with Windows Task Scheduler or cron. The system does not independently create daily batches when the app is shut down.

## Facebook Page

Requires your own FB_PAGE_ID and FB_PAGE_ACCESS_TOKEN (Page token), corresponding permissions and any Meta App Review approvals. Uses the Page Reels flow: initialize, upload MP4 to Meta's returned rupload.facebook.com URL, then finish with published state. The publish API has NOT been exercised against a real Page in this development environment. Meta may require updated API versions, video processing checks, permissions or parameters. Consult official Meta Graph API documentation before publishing.

When an upload request fails with an uncertain remote state the job becomes needs_review and **is not retried automatically**, to prevent duplicate posts. Even a successful finish response is not proof of a fully processed and publicly visible Reel. Confirm on your Page/Business Suite.

## Duplicate prevention and accuracy

SQLite retains both approved and rejected prompts. A SHA-256 fingerprint prevents exact normalized duplicates, and string similarity rejects near-duplicate questions. This will NOT prevent all semantic duplicates, and global answer uniqueness may over-restrict common beginner words. There is no mathematical guarantee of infinite non-repeating questions. Every AI question requires semantic review before export.

## Project map

- main.py: CLI entry point.
- studio/quiz.py: schema, validation, curriculum.
- studio/ai.py: multi-provider AI.
- studio/database.py: SQLite data and job states.
- studio/service.py: workflow orchestration.
- studio/renderer.py: Pillow + FFmpeg.
- studio/webapp.py: localhost dashboard.
- studio/publisher.py: Facebook Page Reels.
- studio/scheduler.py: worker.
- tests/: standard library unit tests.

Run tests:

    python -m unittest discover -s tests -v

## Known MVP limitations

- Online Vietnamese and Korean voice synthesis via edge-tts is available when selecting Voice; music is not included.
- No cloud hosting or unattended publishing when laptop/server is off.
- No production OAuth consent, token renewal, permission verification, or Meta publish status polling.
- No rigorous semantic validation: human editorial review is mandatory.
- No embeddings-based dedupe at high volume.
- The renderer uses a 540x960 base canvas and upscales for larger resolutions; native 1080p typography is a future improvement.
- Daily batch needs external cron/Windows Task Scheduler trigger.

## Windows one-click EXE (v0.4.0)

A Windows GitHub Actions workflow creates a standalone desktop EXE containing Python runtime, Pillow, edge-tts, FFmpeg and FFprobe. Download from the repository Releases (or the Build Windows EXE Actions artifact if release publishing is restricted). Double-click TiengHanSoEasyStudio.exe to start the local dashboard. Data is stored in the user's LOCALAPPDATA/TiengHanSoEasyStudio directory. The EXE does not require installing Python or FFmpeg separately.

## Multiple API keys

The local dashboard has a key pool section. Enter multiple keys separated by commas for Gemini, Groq or OpenRouter, and save. Keys are stored in a LOCAL keys.local.json file, excluded from Git. The file is plaintext on the user's computer; use Windows account/file permissions and do not expose the dashboard publicly. Alternatively configure GEMINI_API_KEYS, GROQ_API_KEYS, OPENROUTER_API_KEYS or the single-key env vars. Requests rotate keys round-robin within a provider. HTTP 429 activates a provider-wide cooldown rather than using another key to bypass the rate limit. Provider fallback remains available.

## Vietnamese and Korean voice

The dashboard now enables narration by default (checkbox). It requests online Vietnamese speech for each question and Korean speech for the correct answer, caches generated MP3 clips, and mixes them into the MP4 with FFmpeg. Audio output must pass an FFprobe stream check. If speech synthesis is unavailable the program raises an error rather than silently producing a mute MP4. The CLI can explicitly request voice with the --voice flag. edge-tts requires Internet and its availability/service conditions can change.

### Validation status

- Offline FFmpeg audio mux is covered by automated tests using generated tone audio without network.
- Online edge-tts and the actual Meta API require separate tests with Internet and user credentials.
- Windows EXE build is performed and published by GitHub Actions on the main branch.

## Windows GUI hotfix (v0.2.1)

Fixes Brave/Chrome ERR_EMPTY_RESPONSE when running the frozen no-console EXE. Python's default HTTP request logger tries to write to sys.stderr, which is None in PyInstaller --windowed. The HTTP handler now sends request and crash logs through the file logger instead. The GUI waits for the health endpoint before opening the browser and shows a usable startup error. Logs: LOCALAPPDATA/TiengHanSoEasyStudio/studio.log. GitHub Actions now launches the actual built EXE in --smoke-server mode and verifies /health, / and /api/status before releasing it.

If you previously downloaded v0.2.0, close it and use v0.2.1 instead. The existing local database and saved API keys stay in LOCALAPPDATA/TiengHanSoEasyStudio.

## v0.3.0: TXT keys and random-topic dashboard

The dashboard now has four tabs (Create / Review & Render / API Keys TXT / Facebook Page). You no longer type a topic. On opening, the app selects a random subject from the curriculum. Click **Đổi chủ đề khác** to reroll without immediately repeating the current subject; selection favors less-used topics. Then click **Tạo câu hỏi chủ đề này**, review draft questions, approve them, and export MP4. The app prevents rendering when no approved questions are selected rather than displaying an unhelpful error. Use the **Chọn câu đã duyệt** button for convenience.

### Import API keys from TXT

In **API Key TXT**, choose Gemini, Groq or OpenRouter as the default service, select a .txt file (up to 64 KB), and click **Nhập key từ TXT**. One key per line, comma-separated lines and comments (lines beginning # or //) are supported. A combined file can contain sections:

    [gemini]
    YOUR_GEMINI_KEY_1
    YOUR_GEMINI_KEY_2
    [groq]
    YOUR_GROQ_KEY
    [openrouter]
    YOUR_OPENROUTER_KEY

Or use lines with provider names (e.g. gemini: MY_KEY). Imported keys are appended to the saved local pool with duplicates skipped, preserving previously saved keys. TXT is read in your browser; the content is sent only to the local server on 127.0.0.1 and is stored as plaintext in LOCALAPPDATA/TiengHanSoEasyStudio/keys.local.json. Delete the original TXT yourself after import if you do not need it. Never commit keys or expose the local dashboard to the Internet.

The publishing section now shows generated MP4 files in a dropdown rather than requiring you to type their full paths. Voice still depends on a working internet connection and the online TTS service. Meta Page publishing still needs a valid Page token and real-world verification.

This version is built by the Windows EXE workflow, which checks the actual bundled UI and the random-topic API before creating the v0.3.0 GitHub Release.

## v0.3.1: Choose and test AI models

The Studio UI in **API & Model** now supports a separate chosen model for Gemini,
Groq, and OpenRouter. Click **Lấy danh sách model từ API** to fetch models available
to your connected key directly from the provider; select one, optionally test it
using **Thử model với API key**, then click **Lưu model đang chọn**.
You can choose a key number in the pool for testing without revealing the key.
The service checks your configured model on every generation request and persists
the choice in the local data folder as models.local.json. No API keys are returned
by the model endpoints.

- GET /api/models?provider=gemini|groq|openrouter: fetch list for an owned key.
- POST /api/models/select: persist {provider, model}.
- POST /api/models/test: submit {provider, model, key_position} to verify one model/key.
- GET /api/status: expose the selected **model IDs**, never actual API keys.

Gemini 2.5 Flash-Lite may return HTTP 404 for some newly created projects even
with a valid key. Use list models to see models accessible to your key. The model
list can include paid-only models; neither listing nor successful testing proves
a free tier. Model test makes a real short API generation request and consumes
some tokens. HTTP 429 causes a provider-level cooldown and must not be bypassed
through key rotation. Each key may have different model entitlements, so test the
keys you intend to use before producing large batches.

v0.3.1 EXE is built by Windows GitHub Actions and smoke-tests that the bundled
UI contains the model selector and that /api/status reports model configuration.
Older local API keys and question databases are preserved on update.

## v0.3.2: TTS fails with NoAudioReceived

Edge TTS occasionally returns `NoAudioReceived` even with healthy Internet. Studio
now retries Edge for each voice, tries an alternate Edge voice, and in the default
Auto mode falls back to gTTS (Vietnamese + Korean). Both Edge and gTTS are
unofficial online dependencies with independent limits and availability; no
internet service is guaranteed. Select **Tự động (Edge → Google)**, **Chỉ Edge**,
or **Chỉ Google**. Before rendering, click **Thử giọng tiếng Việt** and
**Thử giọng tiếng Hàn** to receive playable MP3 samples. These tests are real
third-party requests and need Internet. MP3 bytes are cached locally; no text
or API keys are committed. The render pipeline never silently declares an
audio failure a successful voiced video. If both TTS systems fail, switch off
voice explicitly to create a silent MP4 or investigate your network/service.

Settings for API models and keys remain unchanged. The Windows EXE contains
gTTS in addition to edge-tts, Pillow, FFmpeg and FFprobe.


## v0.4.0 · Production studio

- **Better type rendering:** Vietnamese is laid out with Windows Segoe UI and Korean with Malgun Gothic, explicitly separating font runs, Unicode NFC normalization, multi-line wrapping, and a readable minimum font size. The app uses installed OS fonts, not redistributable fonts. Linux preview requires suitable Noto Sans CJK and DejaVu fonts.
- **Slower, adjustable pacing:** defaults: opening 1.5s, countdown 5.5s, answer 2.8s, transition 0.8s. Video renders at 24 FPS for Facebook Reels. Each question has a soft fade/slide.
- **Consistent speech loudness:** FFmpeg loudnorm is applied to each TTS audio clip, with a peak limiter on the final mix. Original clock ticks, answer chime and transition swooshes are synthesized offline; no sound packs to download. SFX can be disabled and its gain adjusted.
- **Preview:** preview the exact quiz frame before/after answer, plus generate a short motion/SFX MP4 for one question before rendering a batch.
- **Branding:** import PNG/JPEG/WebP logo under 2 MB and adjust scale and corner placement; preview and final export share the same compositor.
- **Storage:** choose custom output and TTS cache folders with the local Windows folder picker. The app stores paths in \`production.local.json\` in your local user-data directory. Existing files are not deleted when changing folders.
- **Batch history:** new SQLite migration adds batch IDs, an exported status, a video registry and SEO metadata, while keeping your old questions. Filter by batch, by draft/approved/rendered/rejected, and paginate 20 questions per page.
- **Bulk publishing workflow:** render selected approved questions in groups of up to six per MP4. Successful groups are recorded even if a later group fails. New MP4 files appear in the media library.
- **Facebook Page connection:** paste your own Page ID and Page Access Token in the Facebook & SEO tab; Studio calls the official Graph API to verify that the Page is readable and stores the token with Windows DPAPI protection for the current Windows user. This is a **manual Page-token connection**, not automatic Facebook OAuth login. A successful read does NOT prove you have pages_manage_posts or Meta App Review approval; those can only be verified through authorized publishing. Never provide your token in chat or GitHub.
- **AI title/SEO drafts:** select a newly generated, registered MP4 and ask the configured AI provider to draft its Facebook title, description and hashtags based on the quiz questions. You must review/edit the text before scheduling. A draft is not silently published.
- **Scheduling:** the scheduler still requires the app to remain running. It uses Meta's Page Reels upload API, and keeps ambiguous network results in \`needs_review\`, with no automated repost. A successful upload response is not proof the Page has already surfaced the Reel.

**Availability:** Edge/gTTS, generative AI models and Meta publishing are internet-dependent, quota/permissions-dependent services. CI runs offline mocks and EXE smoke tests, not live Meta publishing or provider accounts. Always verify the Page, test a voice sample and preview one video before bulk publishing.

**Reels video length:** keep the total duration within your Page's supported Reels length by choosing around 3–6 questions per MP4. You can change timing and output resolution in the dashboard.

**Windows paths and saved secrets:** output/cache paths live only in local settings. API keys are still stored as plaintext in the local key file. Facebook token storage uses Windows per-user DPAPI and cannot be migrated to a different Windows user without reconnecting.



### Export-folder history and honest publishing states

Changing the output directory preserves your previous output directories in local
\`production.local.json\`. The media library can still find MP4s in previously
selected export folders and in the original Studio output folder. No existing MP4
files are moved or deleted.

The app avoids scheduling the same video twice while a scheduled, pending or
uncertain Meta job is already recorded. After Meta accepts the Reels finish call,
a job is marked \`submitted\` rather than publicly \`published\`. Confirm its
public appearance and moderation status on Meta Business Suite. Unexpected
upload failures are marked \`needs_review\` and never blindly retried.


## v0.4.1: Audio-first short quiz and romanized correct answers

- Newly AI-generated quiz items must include short Vietnamese \`clue\` (1–6 words),
  Hangul correct answer, its **Revised Romanization** \`romanization\`,
  and verified Vietnamese \`meaning\`. The full explanation is no longer read aloud.
- Existing 0.4.x SQLite questions are preserved. Common verbose prompts are
  shortened for on-screen display and speech without modifying the original
  question text in the database.
- **Narration before countdown:** actual Vietnamese TTS MP3 duration is measured
  with bundled FFprobe. The full countdown starts only after the clip completes,
  with a short pause. Countdown ticks and answer chime follow scene timestamps.
- Correct-answer screen shows highlighted Hangul, romanization and Vietnamese
  meaning. When a legacy quiz has no romanization, an approximate orthographic
  fallback is displayed with an explicit check-me note. This is NOT a fully
  accurate phonological pronunciation engine for irregular sound changes.
- The Korean TTS runs after the answer reveal. If the Korean clip takes
  longer than the configured reveal time, that scene extends automatically.
- Preview with voice enabled generates a one-question sample using actual online
  speech and the exact same audio-first timing as production MP4. Preview with
  voice disabled uses a short fixed prompt stage.
- The question bank displays short clue and the available meaning/romanization
  so reviewers can catch mistakes before approving.
- TTS requests still require Internet and provider availability. Model output
  is not a dictionary: people should verify translations and romanization
  before bulk publishing.



## v0.4.2: Four-choice pronunciation, MP4 copyright metadata, draggable logo, silent console and export progress

- Korean quiz questions now store **romanization + Vietnamese meaning for all four choices**:
  \`romanization\` / \`meaning\` for the correct answer, and
  \`wrong1_romanization\` / \`wrong1_meaning\` through \`wrong3_*\` for distractors.
  All choices show their individual reading and meaning **on reveal**, not before
  the countdown. The currently selected correct answer remains highlighted.
- AI-generated questions request all eight fields for the four answers.
  Older local questions remain readable unchanged. Use **AI bổ sung nghĩa/phiên âm**
  for up to six selected questions, review manually in **Sửa 4 đáp án**, and
  approve them again before exporting. Missing meanings are displayed as missing;
  the app will not invent translations from context.
- Video production settings now hold four metadata fields: title, artist, copyright
  statement and comment/website. New exports are tagged with FFmpeg stream copying
  (no extra video encoding), and **Ghi đè metadata** can re-tag an existing output
  safely after validating a temporary replacement. Scheduled videos are protected
  from accidental re-tagging where registered. **Metadata is not DRM, platform
  Content ID, proof of ownership or protection from downloading/re-encoding.**
- To position the logo, upload PNG/JPEG/WebP, open the static preview and drag the
  image anywhere in the video frame. X/Y are stored as normalized fractions of the
  available width/height for the scaled logo. Select a preset corner at any time
  or save the custom position. Final video and motion previews reuse this layout
  at both 540×960 and 1080×1920.
- **Export status:** /api/render/start creates a local single-worker job, and
  /api/render/progress?job_id=... reports phase and monotonically increasing
  percent. The dashboard polls and shows a progress bar, completed MP4 count,
  and any per-job error. Progress is based on pipeline stage and encoded frame
  count, not elapsed time, so it can advance unevenly. A job reaches 100% only
  after the MP4 file, its metadata and its registry entry have completed.
- On Windows every bundled FFmpeg and FFprobe child process in production
  runs using CREATE_NO_WINDOW. No extra black terminal windows should flash
  during export. The Studio window remains available, and one batch at a time
  avoids output-file contention.
- The previous synchronous /api/render and /api/render/batch endpoints remain
  for backwards compatibility. New UI uses the asynchronous endpoints.
- All API credentials remain in local user data. The new metadata/editor endpoints
  never send credentials to the browser. Existing locally saved question and
  video history is preserved.



## v1.0.0 Final Production Suite

**Báo cáo audit:** xem AUDIT_FINAL.md ngay tại thư mục gốc repository, gồm risk register, kiến trúc, release gates và giới hạn thực tế.

### Autopilot (không chọn từng câu)
- Mở mục **Sản xuất tự động**. Chọn 1–12 MP4/lượt, 1–6 câu/video, dải TOPIK, format, template, giọng đọc và profile.
- Engine chọn topic ít sử dụng và **không trùng topic trong cùng một lượt**, tạo câu mới qua AI, kiểm tra đủ nghĩa/phiên âm của A/B/C/D và chống trùng đáp án. Các câu đạt kiểm tra cấu trúc nhận trạng thái *auto_approved* riêng; nội dung chưa đủ nằm lại trong ngân hàng để duyệt. Machine check không chứng minh ngữ nghĩa tiếng Hàn đúng.
- Từng video xuất xong được ghi vào SQLite. Lượt sản xuất được ghi ở production_runs. Khi app khởi động lại sau crash, run chưa kết thúc được đánh dấu *interrupted*; video đã thành công không bị xóa.

### Video Shorts & Reels
- Mặc định đối với cấu hình mới: **1080×1920, 30 FPS**, H.264 yuv420p, AAC 48kHz. Cấu hình *Vùng an toàn Shorts/Reels* thu nhỏ vùng chữ để tránh các vùng overlay trên/dưới/bên phải.
- Logo kéo trên preview sử dụng cùng layout khi xuất MP4. Metadata title/artist/copyright/comment được gắn tự động trong bước render.
- Kể cả khi tắt TTS và SFX, video vẫn có một track AAC silent chuẩn thay vì không có audio.
- Tên MP4: THSE_YYYYMMDD_NNNNNN_topic_TOPIKn_1080x1920.mp4. Số thứ tự atomic trong SQLite, ngày theo giờ Việt Nam (+07:00).

### Cache
- Mục **Dọn cache & nhật ký** hiển thị tổng dung lượng của MP3 đã tạo, mẫu đọc và video preview. Có thể xóa toàn bộ hoặc chỉ những file cũ hơn 1/7/30 ngày.
- Chỉ xóa các file được đặt tên theo quy ước Studio. Không đụng tới database, MP4 đã xuất, logo, API keys hay thông tin Page. Không xóa khi một export đang chạy.

### Multi-platform publishing
| Nền tảng | Kết nối và xuất bản | Yêu cầu |
| --- | --- | --- |
| Facebook Page | Page Reels API như cũ | Page ID, Page Access Token, các quyền phù hợp |
| YouTube Shorts | OAuth Desktop client JSON, Google youtube.upload, resumable videos.insert | Google Cloud project & consent, quota; mặc định Private |
| Instagram Reels | Graph API Professional, xử lý container rồi media_publish | IG User ID/token và URL MP4 HTTPS công khai có thể tải từ Meta |
| TikTok | Sẵn MP4 và sao chép caption để đăng thủ công | Direct Post public yêu cầu audit và user consent; bản cá nhân không bypass |

Để thiết lập YouTube: tạo Google Cloud OAuth client kiểu Desktop, tải client JSON, nhập vào mục Đăng đa nền tảng, bấm kết nối và xác nhận trong trình duyệt. App lưu OAuth token bằng DPAPI trên Windows và chỉ yêu cầu quyền upload.

Instagram yêu cầu MP4 đã nằm trên URL HTTPS public (ví dụ ở CDN tự quản lý). Studio không tự host MP4 hoặc upload đến Instagram từ đường dẫn C:\\.

Lịch đăng trên từng nền tảng được lưu độc lập trong SQLite, chống đăng trùng MP4 cùng nền tảng. Các upload mạng có kết quả không chắc chắn nhận trạng thái *needs_review*, không tự đăng lại. Bạn phải giữ Studio/worker hoạt động khi tới giờ hẹn.

### Bảo mật và giới hạn
- Pool API keys cũ được migrate từ JSON plaintext sang DPAPI per-user Windows. Dữ liệu trên Linux sử dụng chmod 0600.
- Không lưu password Meta/Google trong app, không bỏ qua API app review/quota.
- **Metadata video không phải DRM hay chứng cứ pháp lý sở hữu.**
- Nếu chưa có kiểm chứng về dịch Hàn-Việt, hãy xem lại mẫu ngẫu nhiên trước khi đăng công khai hàng loạt.
- Các bài test CI dùng mock Google/Meta; việc upload thật vẫn phải kiểm thử với tài khoản cá nhân đã được cấp quyền.



## v1.1.0 · Template Hàn Quốc, Auto QA repair, Sự kiện

Đọc báo cáo audit nguyên nhân và cách sửa: [AUDIT_V110.md](AUDIT_V110.md).

### Video rõ nét, có họa tiết
- 540x960 / 1080x1920 giữ bố cục chính gần như toàn khung. Bỏ lỗi v1.0.0 co nội dung xuống 421x749 làm chữ nhỏ và không cân đối.
- Họa tiết chìm từ hình vector tự vẽ: mái cung điện Hàn Quốc, tháp N Seoul, hoa Mugunghwa, dải dancheong, nét giấy hanji. Mỗi template có sắc độ khác nhau, chữ và đáp án vẫn đứng trên lớp đồ họa.
- 30 FPS được giữ khi lưu cấu hình giao diện. Cần render một MP4 thử để xem trên điện thoại và kiểm tra các vùng overlay thực của mỗi nền tảng.

### Autopilot 0/N cần kiểm tra
- Không còn loại câu vì **cụm gợi ý không trùng nguyên văn nghĩa từ** (ví dụ “Giá rẻ nhưng” so với “nhưng”).
- Sinh thêm ứng viên câu hỏi, có vòng thử lại giới hạn, AI tự bổ sung nghĩa/phiên âm còn thiếu cho cả bốn đáp án trước khi quyết định đưa câu vào video.
- Nếu thiếu dữ liệu/AI hết quota, không tạo ra đáp án giả: giữ câu ở dạng nháp, hiển thị nhóm lý do. 0/N sẽ có trạng thái **Cần bổ sung câu hỏi**, không phải hoàn thành video thành công.
- Chỉ xác minh cấu trúc, **không thể thay thế kiểm tra độ chính xác tiếng Hàn**.

### Chủ đề theo sự kiện là mục riêng
- Autopilot thêm lựa chọn: **Chỉ ngẫu nhiên**, **Trộn 1 video sự kiện + phần còn lại ngẫu nhiên**, **Ưu tiên sự kiện**.
- Có bảng đề xuất sự kiện theo giờ Việt Nam trong vòng 45 ngày: 25/12 Noel/크리스마스, 1/1 năm mới, 1/3 삼일절, 5/5 어린이날, 15/8 광복절, 9/10 한글날, 11/11 Pepero.
- Các lễ âm lịch như Seollal/Chuseok cần nguồn niên lịch chính xác nên chưa tự gắn ngày.
- Không trộn sự kiện với random khi chưa chọn.

### Tự điền metadata và giao diện gọn
- Với MP4 mới, mặc định tự đặt **title theo chủ đề + cấp độ**, **artist: Tiếng Hàn So Easy**, **copyright: © năm hiện tại Tiếng Hàn So Easy**, **website: tienghansoeasy.io.vn**. Không phải điền tay hoặc bấm ghi đè sau khi render.
- Các tùy chọn sửa MP4 cũ được chuyển vào mục nâng cao, vì ghi đè video đã lên lịch có rủi ro.
- Nhóm xuất thủ công được thu gọn, không trộn với Autopilot. Bố cục form dùng CSS grid đáp ứng màn hình lớn/nhỏ.

Cách cập nhật: đóng Studio cũ; sao lưu thư mục LOCALAPPDATA/TiengHanSoEasyStudio, rồi mở EXE mới. Dữ liệu hỏi đáp và kho video cũ được giữ lại. Meta/YouTube/TikTok vẫn phải tuân thủ quyền API như báo cáo v1.0.0.
