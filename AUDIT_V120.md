# FINAL AUDIT v1.2.0 · KRDict Evidence, Independent Reverse QA, Auto Thumbnail

## 1. What screenshots revealed

The auto-generation workflow saved *every generated question* as `draft`.
Only candidates selected for an MP4 were promoted to `auto_approved`, and
rendering immediately advanced them to `rendered`. Spare or rejected questions
remained draft and the UI misleadingly called all such rows "Chờ duyệt".
This is not proof that the AI checked them and rejected them: many had never
been checked at all.

**Fix:** A separate `question_qa` table persists `not_checked`, `verified`,
`needs_review`, `unavailable` and `stale` outcomes, findings and source tier.
Production `questions.status` continues to indicate manual approval,
auto approval, rendered or rejected. Every generated candidate seen by
Autopilot is structurally checked and receives a QA record. Verified spare
candidates remain approved and ready for later videos; unresolved candidates
show the actual reason. Editing translations resets approval and marks QA stale.

## 2. Verification algorithm

1. **Structure:** four distinct Korean choices, reasonable lengths,
   answer meanings + Revised Romanization for A/B/C/D, no blank fields.
2. **Official dictionary:** use Korean Basic Dictionary (National Institute
   of Korean Language) search API with user-owned 32-hex key and
   translated=y, trans_lang=7 (Vietnamese). Exact headword only. Examine
   translated senses and headword IDs. Cache dictionary responses in SQLite,
   never store API key in cache or QA reports.
3. **Blind reverse-answering:** use a configured AI model, preferentially
   a *different provider* than the generating model. Shuffle A/B/C/D;
   do not pass original correct label, model's meanings or explanation.
   Require selected Korean answer to equal the original, no ambiguity,
   no conflicting alternatives, and a sufficiently matching Vietnamese
   back-translation. A second AI call is corroboration, not independent
   certainty if the underlying models share biases.
4. **Distractors:** dictionary translations of wrong Korean options must
   not also substantially match the clue. If they do, the question is
   ambiguous and needs review.
5. **Thresholds:** strict mode requires exact KRDict word + Vietnamese
   overlap **and** blind reverse AI agreement. Assisted mode can accept
   clear phrases absent from the dictionary if blind AI passes; the tier
   is `blind_ai_only` and is not described as dictionary-verified.
6. **Fail closed:** key missing, quota, bad XML, no reviewer, uncertain
   context or disjoint translations means `needs_review`. Do not insert
   invented synonyms or silently approve uncertain Korean.

### Important linguistic caveats

Vietnamese overlap is a **lexical heuristic**, not a sentence-level semantic
entailment model. Polysemy, Korean inflections, fixed phrases and compound
words are not reliably settled by a dictionary search alone. Exact headword
presence does not prove that the requested contextual sense is appropriate.
AI reviewers can agree on a mistake. Spot-review a sample before scheduled
public posting.

## 3. Dictionary API

Official documentation:
https://krdict.korean.go.kr/vie/openApi/openApiInfo
https://krdict.korean.go.kr/api/search

Authentication uses a 32-character hexadecimal key. API supports Vietnamese
translations with `translated=y&trans_lang=7`; official daily limit
documented as 50,000 requests. The Studio uses bounded calls and
SQLite cache to avoid repeated queries. Users import keys in **API & Model**
with provider KRDict or a TXT `[krdict]` section; no external secrets
are included in the repository or build.

## 4. Thumbnails

- Each newly rendered MP4 gets an accompanying `*_thumbnail.jpg` from the
  actual branded 9:16 introductory frame, including topic and level.
  Image lives beside the MP4 and is available for preview/download through
  the local Studio dashboard. A bounded legacy operation creates JPGs
  for older MP4s, with best-effort topic (past theme may not be recorded).
- YouTube Shorts supports `thumbnails.set` after video processing succeeds.
  The publisher queues only the image action in SQLite, checks processing
  status and retries with backoff. **A thumbnail failure never retries the
  already-uploaded MP4**; the queue status is visible.
- Facebook Page Reels and Instagram Professional may choose a frame on
  their own. There is no reliable documented image-cover upload in the
  existing Reels publishing methods used by this application. The JPG is
  still generated and downloadable, but we do not assert a custom thumbnail
  was attached via Meta unless the API actually supports and confirms it.

YouTube reference:
https://developers.google.com/youtube/data/reference/rest/v3/thumbnails/set

## 5. Acceptance / QA

- SQLite migration preserves old questions, registered videos, API keys,
  quotas and schedules. No automatic rewrite of old content.
- New and legacy quiz rows have both production status and QA outcome.
- Wrong-answer dictionary overlap, absent dictionary record, reverse
  mismatch, ambiguous AI output and missing API keys block strict approval.
- Cache and key secrecy verified through tests with mock API responses.
- Every new render creates a deterministic JPG from the branded opener,
  not a generated image unrelated to the quiz.
- YouTube thumbnail delivery is retryable independently of MP4 upload.
- Linux/Windows tests + packaged Windows EXE HTTP smoke must succeed before
  public release.
- Live KRDict, Google and Meta publishing require user-provided credentials
  and cannot be proven from mocked CI. The user must confirm live posting
  rights and review linguistically uncertain content.

## 6. Remaining risks

No machine-only process can promise zero translation errors. Dictionary
coverage is not unlimited. YouTube custom thumbnails may require a verified
channel and can be rejected after upload; a queued retry does not guarantee
success. Meta's APIs may not attach the downloaded JPG to Reels. Scheduled
publishing still requires Studio/worker running on the due date. Copyright
MP4 metadata is attribution rather than DRM or proof of exclusive rights.
