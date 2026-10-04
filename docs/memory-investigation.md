# Memory investigation

Branch: `codex/reduce-memory`. Measured on Windows on 2026-10-04.

## Implemented

The formatted editor now requests a bounded display preview through the existing
Qt bridge. Static attachments larger than 1240 × 680 pixels are scaled with
`QImageReader`, preserving aspect ratio and EXIF orientation. Originals are never
rewritten; captures, copy, the canonical document, and undo retain their original
Markdown paths. Small and animated images use their original files. Preview paths
are restricted to the draft's attachment directory, matching the existing local
resource policy.

Previews are cached on disk, keyed by original path, byte size, and modification
time. Python does not retain decoded preview images. Cache failures fall back to
the original display asset. The cache trades a small amount of disk space for
lower display memory and avoids decoding the original again after tray reopening.
Some image plugins decode the full original before scaling, so first-load peak
memory and GUI-thread decode latency are not eliminated. Animated images remain
unbounded by the preview size, preserving their current playback.

The existing 30-second renderer suspension and all editing behavior remain in
place. No browser sandbox or process isolation settings were weakened.

## Measurements

`tools/measure_memory.py` creates a disposable draft without hotkeys, IPC, or
clipboard access. It measures the fixture's Python process and **all descendants**
using Win32 process enumeration. Unrelated apps are excluded. It uses a deliberately
large 1000 × 1500 window so three 4000 × 3000 JPEG previews are present at once.
These numbers describe this fixture, not the user's normal window or real draft.

One paired run, MiB (working set / private committed memory):

| Phase | Original image display | Bounded image previews |
| --- | ---: | ---: |
| Text only | 390.1 / 225.8 | 392.3 / 225.7 |
| Three 12-megapixel images | 433.3 / 245.3 | 416.1 / 243.6 |
| Suspended in tray | 325.7 / 212.3 | 318.3 / 212.4 |
| Reopened | 437.6 / 248.4 | 418.0 / 244.6 |

In this sample, previews reduced visible working set by 17.2 MiB (about 4%),
and after reopening by 19.6 MiB. Private-memory savings were much smaller.
Actual images, display scaling, graphics drivers, window size, allocator state,
and Windows paging affect the result. An earlier unpaired baseline varied
substantially, so these are observations rather than a guaranteed saving.

The preview images were 906 × 680 pixels; all three full originals remained
4000 × 3000. A plain native Qt editor fixture measured 113.5 / 53.4 MiB for text
only, and 114.2 / 53.7 MiB with three image references. It displays the paths
as text and **does not implement formatted editing or image previews**. Its
roughly 71% lower text-only working set shows potential, not a feature-equivalent
replacement's achieved memory consumption.

Software Chromium rendering was also measured: 377.3 / 207.0 MiB for text,
408.2 / 220.0 MiB with previews. It still uses Chromium and trades graphics
acceleration for CPU work; that experiment does not justify changing the default.

Reproduce each run in a fresh process:

```powershell
.\.venv\Scripts\python.exe tools\measure_memory.py --original-images --output artifacts\memory-original-images.json
.\.venv\Scripts\python.exe tools\measure_memory.py --output artifacts\memory-previews.json
.\.venv\Scripts\python.exe tools\measure_memory.py --native --output artifacts\memory-native.json
.\.venv\Scripts\python.exe tools\measure_memory.py --software --output artifacts\memory-software.json
```

`--original-images` bypasses only the preview resizing for an A/B comparison.
`--native` uses the existing `web_enabled=False` test editor. Neither changes
the production app's editor selection.

## Replacing Chromium while preserving behavior

CodeMirror requires a browser DOM. Replacing Qt WebEngine with WebView2 can keep
CodeMirror, but [Microsoft documents that WebView2 uses Chromium](https://learn.microsoft.com/en-us/microsoft-edge/webview2/).
It would require a new embedding and bridge implementation, and it would not
meet the goal of removing Chromium. Its memory benefit has not been measured.

A true non-Chromium option is native Qt editing. The existing canonical
`PromptEditor` document and capture logic can be retained, but the formatted
editor needs a native implementation. Merely showing `PromptEditor` would remove
live formatting, inline previews, the slash menu, and formatting commands.

The existing composer interface is the seam: the GUI expects copy/cancel/image
signals, focus, loaded/error signals, and shutdown. A native adapter can satisfy
that interface while keeping source Markdown authoritative. It must apply source
edits directly and paint formatting or map a display layout back to source
offsets; serializing a rich-text document back to Markdown would violate exact
text preservation.

Before making a native adapter the default, validate:

- Headings, emphasis, strikethrough, lists, tasks, quotes, links, and fences,
  including delimiter reveal near the caret and embedded backticks.
- Every toolbar command, Ctrl+B/Ctrl+I, the slash menu, paste, and image previews.
- UTF-16 caret/selection positions, Unicode, capture insertion, normal undo/redo,
  Undo Last Capture, and reversible Clear.
- Exact source/formatted round trips and full-document copy, preserving whitespace.
- Image attachment/paste, original paths, animation, and local-only resource access.
- Draft persistence, tray reopening, focus restoration, and captures while hidden.
- Full-process memory and editing responsiveness with the same fixtures and a
  realistic draft, rather than just the plain-text native lower bound.

This branch implements the preview reduction and records the replacement path;
it does not claim that a native formatted editor has been implemented.

## Validation

- 53 unit tests passed, including preview dimensions, original-byte preservation,
  cache reuse/invalidation, local path restrictions, GIF preservation, and cache
  failure fallback.
- The actual bundled WebEngine editor check passed: formatted/source switching,
  toolbar edits, typing, caret synchronization, captures, undo, Ctrl+Enter, image
  previews, suspension races, undo/redo restoration, and capture while asleep.
- The offline JavaScript bundle was rebuilt with the repository's esbuild command.

Qt emits graphics-context fallback diagnostics during the software-rendering
fixture; the editor still loads and the checks pass.

References: [Qt scaled image decoding](https://doc.qt.io/qt-6.8/qimagereader.html#setScaledSize),
[Qt WebEngine flags](https://doc.qt.io/qt-6.8/qtwebengine-debugging.html#using-command-line-arguments),
[native Qt plain-text editor](https://doc.qt.io/qt-6/qplaintextedit.html).
