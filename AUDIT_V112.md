# v1.1.2 Audit Addendum: Native Hangul & Windows MP4 Metadata

## User-visible findings

1. Korean characters in the sample video are readable, but appear slightly soft/blocky when compared with crisp 1080p typography. The renderer painted the entire card/text at 540x960, then upscaled to 1080x1920. Mixed Vietnamese/Hangul labels also used per-font top anchors, yielding inconsistent baselines.
2. Windows Explorer → Properties → Details shows empty Author/Copyright. Previous MP4 mux used FFmpeg `-movflags +faststart+use_metadata_tags`, producing **mdta** atoms. FFprobe recognizes these tags, but standard players/Windows shell handlers commonly ignore them.
3. Windows Explorer itself does not guarantee display of every MP4 tag, even when the MP4 contains standard copyright atoms. An empty Explorer Copyright field alone is not proof that the tag is missing.

## Changes

- Render at native configured 540 or 1080 width, using a logical-coordinate drawing adapter with FreeType font rasterization at the true output pixel size. Do not enlarge a 540px source raster to 1080px.
- Mixed Korean and Vietnamese glyph runs are aligned against a common baseline (`anchor="ls"`) instead of individual top bearings. Font choice remains Windows Malgun Gothic and Segoe UI (already on Windows), no extra font downloads/distribution.
- Existing layout/Shorts inset and logo drag coordinates retained.
- MP4 metadata writer now uses standard `ilst` atoms with `-movflags +faststart` (not `use_metadata_tags`), and supplies title, artist, copyright, comment, year, description and genre.
- FFprobe verifies essential tags and positive duration **before replacing** the original MP4; errors leave original untouched.
- Studio adds a visible single-video metadata inspector (title/author/copyright/comment/year/type) and one-click repair for up to 20 older MP4s, skipping files scheduled for publishing.
- Compatibility warning in app: Windows Explorer does not show all MP4 metadata fields.

## QA and limitations

- Test native glyph size, direct 1080p drawing and unchanged 9:16 portrait output.
- Test legacy mdta → standard ilst conversion using real FFmpeg/FFprobe, including presence of the cprt atom, readback validation and preservation of source when fields are invalid.
- Test bulk repair excludes scheduled videos.
- Run Windows unit tests and packaged EXE smoke before merging to main.
- No claim of guaranteed Windows Explorer display; use the Studio inspector / FFprobe as the file-level source of truth.
- MP4 metadata signals attribution, not DRM, Content ID, registered copyright or prevention of re-uploads.
- Existing video pixels do not change when only metadata is repaired. Old text can be sharpened only by re-rendering from its saved quiz data.
