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

## Windows one-click EXE (v0.2.1)

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
