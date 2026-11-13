# AUDIT HOTFIX v1.1.0 | TEMPLATE, AUTOPILOT, EVENT, METADATA, UI

Date: 2026-11-13, Asia/Bangkok +07:00. Baseline: main v1.0.0 commit cedd91ef.

## Priority findings

| ID | Priority | Finding | Fix |
| --- | --- | --- | --- |
| V01 | Critical visual | Shorts safety renderer resized 540x960 to 421x749 and pasted off-center | 492x875 centered at x=24,y=33 inside 540x960; preserve readable text and lower overlay margin |
| V02 | High | Strict gate rejected contextual clue "Giá rẻ nhưng" vs answer meaning "nhưng" | Remove false equivalence between context and dictionary meaning; retain strict 4-answer field checks |
| V03 | High | Autopilot generated once and rejected incomplete per-answer meanings | Try limited spare candidates and at most 2 generation passes; enrich missing fields using configured AI providers; no invented translations |
| V04 | High | 0/4 MP4 showed failed 100% without a clear explanation | Mark needs_review; distinguish topics processed from MP4 created; persist per-question QA reasons |
| V05 | Medium | Frontend always overwrote 30 FPS with 24 FPS | Use 30 FPS in saved production settings |
| V06 | Medium | Users had to manually fill author and copyright despite default metadata | Restore blank old values to brand defaults and generate topic/year/artist/site automatically for every new MP4 |
| V07 | Medium | Random topics ignored holidays | Separate opt-in random, 1 seasonal + rest random, and seasonal priority modes; upcoming event discovery using fixed-date calendar |
| V08 | Medium | Unaligned controls, redundant batch UI | CSS grid with responsive breakpoints; manual export and legacy metadata options collapsible; Autopilot remains main production path |
| V09 | Medium | Monochrome 9:16 templates | Original low-opacity Korean artwork: palace eaves, N Seoul Tower, Mugunghwa, dancheong trim, paper brush textures, theme-specific colors |

## Release verification

- Automated tests Linux and Windows plus packaged EXE boot smoke.
- Show Christmas 2026 as 42 days away on November 13, 2026.
- Do not guess Seollal/Chuseok lunar dates, which vary by year.
- Random topics remain independent unless seasonal mode is explicitly selected.
- Verify Korean visual layers without clipping, rendered frames retain 9:16.
- Verify 4 answer meanings and romanizations; quizzes that AI cannot repair stay draft and are not auto-rendered.
- Verify blank/old metadata settings migrate to safe default brand values, topic and year tags added automatically.
- Respect rate limits for each provider and do not bypass platform permissions.

## Residual limits

AI linguistic accuracy cannot be guaranteed by structural checks. A sample human review is necessary before posting a bulk batch. If API keys or TTS are unavailable, the app must display actionable errors rather than produce pretend success. Copyright metadata is attribution, not DRM. YouTube and Instagram publishing need individual account OAuth/permissions; TikTok public Direct Post requires app review. An EXE must stay running when scheduled tasks are due. Screenshots and automated tests do not replace checking rendered video on the user's own monitor.
