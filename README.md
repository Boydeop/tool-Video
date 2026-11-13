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

