<p align="center">
  <img src="src/prompt_scratchpad/assets/logo.png" alt="Universal Annotation app icon" width="112" height="112">
</p>

# Prompt Scratchpad

A small Windows desktop Markdown composer for AI coding prompts. Select text in another app, capture it at the scratchpad caret, add instructions, and copy the complete prompt into Herdr, Orca, Codex, or any other agent.

## Run

Python 3.12+ on Windows is required. From this folder:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Setup.ps1
.\Launch.cmd
```

The setup creates a local `.venv` and installs PySide6, pywin32, comtypes, pynput, and pyperclip. Launch opens the desktop window without a persistent terminal window. A second launch shows the existing scratchpad. Nothing starts automatically at Windows login. Windows' native `RegisterHotKey` owns the app's global shortcuts; pynput is used by the live fixture tests.

Create **Universal Annotation** shortcuts on your desktop and in the Start menu:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Create-Shortcuts.ps1
```

The shortcuts use the app icon and launch `.venv\Scripts\pythonw.exe` directly, without a terminal window. Keep this folder in place; rerun the script if you move it.

For a console with diagnostics:

```powershell
.\.venv\Scripts\python.exe -m prompt_scratchpad
```

## Use

| Action | Shortcut / interaction |
| --- | --- |
| Capture existing selection | **Ctrl+Alt+A** in the source app |
| Arm temporary capture mode | **Ctrl+Alt+Shift+A** |
| Complete capture mode | Select text, then **Ctrl+Alt+A** |
| Cancel capture mode | **Esc**, repeat the mode shortcut, or wait 30 seconds |
| Copy entire Markdown document | **Ctrl+Enter** while the scratchpad is focused, or Copy Prompt |
| Capture text already copied manually | Capture Clipboard |
| Remove latest unchanged capture | Undo Last Capture |
| Regular text undo / redo | **Ctrl+Z / Ctrl+Y** |
| Keep floating window above other apps | Always on top |
| Hide / reopen | Close to tray; double-click the tray icon to reopen |
| Exit completely | Tray menu → Quit |
| Attach an image | **▧ Image** in the editor toolbar, or paste a screenshot with **Ctrl+V** |

The editor is an offline CodeMirror 6 Markdown component inside PySide6's WebEngine view. Its default formatted editing mode displays headings, emphasis, lists, quotes, and fenced code blocks directly in the editable document. Markdown delimiters are revealed around the caret so they remain easy to edit. The toolbar inserts headings, bold, italic, strikethrough, lists, tasks, quotes, code blocks, and links. Type `/` on an empty line for the block menu. **Source** switches to full raw Markdown; **Formatted** returns to the live view. Switching views never serializes or rewrites your text.

Image attachments are copied into an `attachments` folder beside the draft. The formatted editor displays a preview; the raw Markdown contains a local absolute image path such as `![Screenshot](<C:/.../attachments/id.png>)`. Copy Prompt preserves that reference exactly. Move the image file with the prompt if the receiving agent needs the image on another machine. Image files are limited to 20 MB and 50 megapixels.

Captures insert at the active caret and preserve an existing selection in the scratchpad. A capture becomes one ordinary editor undo operation. Undo Last Capture removes only the most recent tracked capture; if you edited it, the app asks you to use normal text undo or delete it manually. Clear can be undone with Ctrl+Z. The count tracks inserted contexts, rather than parsing all Markdown headings; text restored through ordinary redo may need recapturing to regain capture tracking.

Capture mode is explicitly a **select, then confirm** interaction. Windows does not provide dependable universal selection-change events across editors, terminals, and browsers. The app leaves source focus alone when armed, waits for confirmation, then exits after one successful capture. It never polls applications looking for selections.

Generic contexts look like this:

````markdown
### Context — Cursor — main.py

```text
<selected text>
```
````

The capture does not summarize or rewrite selected text. Embedded backticks use a longer outer fence so they cannot break the Markdown. The editor uses LF line endings. Copy Prompt copies exactly the editor's complete plain-text document, including code fences and whitespace.

In Cursor and VS Code, install the bridge below to capture a saved file reference instead of code. Ctrl+Alt+A inserts only the path and one-based selected line range, for example:

```markdown
### Code reference — Cursor

File: C:/repo/src/main.ts
Lines: 12–18
```

When the editor bridge has no current saved-file location, the hotkey shows a status message and does not copy selected code. Captures from browsers, terminals, and other apps keep their full selected text.

## Windows capture behavior

The app remembers the foreground window, waits for the hotkey to be released, and first asks Windows UI Automation for [selected text ranges](https://learn.microsoft.com/en-us/dotnet/api/system.windows.automation.textpattern.getselection) within that window. Supported controls can be captured without changing the clipboard. Provider calls have short timeouts, and the ancestor walk has a one-second budget. The app never uses the control's entire value as a substitute for a selection.

For unsupported controls, the app backs up supported clipboard formats, sends an app-aware copy shortcut, waits at most 1.5 seconds for the copy result, and restores the previous clipboard before bringing the scratchpad forward. An intermediate empty clipboard is allowed while the source finishes its copy. Focus changes cancel capture. Clipboard checks are bounded to this one copy transaction; there is no background clipboard/selection monitor.

- Codex and known browsers receive **Ctrl+C**, their normal copy binding. Windows Terminal receives **Ctrl+Shift+C**. Other apps, including code editors that can host terminals, receive **Ctrl+Insert** after direct accessibility capture is unavailable. Shell windows never receive Ctrl+C as a fallback.
- App shortcuts vary. When these copy shortcuts are unsupported, or an elevated app blocks synthetic input, copy using the app's own command and click **Capture Clipboard**.
- Capturing a selection does not paste, deselect, or type into the source application. The synthetic shortcut can still conflict with an app's custom keybinding.
- No copied selection means an error; old clipboard text is never inserted as a successful capture.
- Text and memory-backed clipboard formats such as HTML/RTF and DIB/DIBV5 images are backed up. The synthesized CF_BITMAP handle is omitted when a DIB pixel buffer is available. GDI-only images, metafiles, pointer-backed objects, unknown formats that cannot be snapshotted, and backups over 32 MB cause the clipboard fallback to stop before changing anything. Direct accessibility capture is unaffected by these clipboard objects.
- If the clipboard changes before capture starts or before restoration, the app leaves the new clipboard alone and reports the conflict. External clipboard changes during the source copy itself cannot always be distinguished from that copy.
- Clipboard history managers may observe temporary clipboard writes. The internal sentinel opts out of Windows clipboard history/cloud upload, but third-party observers are outside the app's control.
- Generic capture supplies app/window metadata. File path and line numbers come from the editor bridge; they are never guessed from window titles.

Implementation follows Microsoft's [clipboard memory/ownership rules](https://learn.microsoft.com/en-us/windows/win32/dataxchg/clipboard-operations). Qt's [local-server access options](https://doc.qt.io/qt-6/qlocalserver.html) restrict the bridge to the current user. Windows named pipes permit multiple listeners, so a separate `QLockFile` guards the running app instance.

## Draft persistence

One atomic JSON draft lives under Qt's Windows application-data location, normally `%LOCALAPPDATA%\PromptScratchpad\Prompt Scratchpad\draft.json`. It stores the Markdown, capture positions, window geometry, and on-top preference. Text saves after 600 ms without typing and when hiding/quitting. No SQLite database is needed. Drafts are local plain text, and there are no network calls in the app.

Use `--draft C:\path\draft.json` to choose a different draft location. If a draft is corrupt, it is preserved and the app uses `draft-recovered.json`. Save failures are shown, and quitting waits for an active clipboard transaction to restore the clipboard.

## Cursor / VS Code bridge

The local TypeScript extension is in `extension/`. Build and install it in Cursor or VS Code; repeat the install for each editor you use:

```powershell
cd extension
npm ci
npm run package
cursor --install-extension .\prompt-scratchpad-bridge.vsix
```

Use `code --install-extension` for VS Code if its CLI is installed. Reload the editor window after installation. The extension sends only the active saved file path and selected line range, never selected code, over the same-user named pipe `\\.\pipe\prompt-scratchpad-v1`. It uses editor selection-change events, with no polling. The app accepts a recent editor location only when the foreground editor window title matches the file. The extension cannot provide a path for an unsaved buffer or an integrated terminal selection.

The pipe also accepts explicit generic text captures from other local clients:

Send one UTF-8 JSON object followed by a newline per connection:

```json
{
  "version": 1,
  "type": "capture",
  "application": "Cursor",
  "window": "main.py",
  "workspace": "C:/repo",
  "file_path": "C:/repo/main.py",
  "start_line": 12,
  "end_line": 18,
  "language": "python",
  "text": "def example():\n    pass\n"
}
```

Only `version`, `type`, and `text` are required for explicit captures. Line numbers are positive, one-based, and inclusive. An `end_line` requires a `start_line`. Text is limited to 1 MB in UTF-8; messages to 1.1 MB, metadata fields to 4096 characters, connections to 8, and incomplete requests to 5 seconds. Unknown fields and malformed payloads are rejected. Incoming context enters the same Markdown formatter/editor as global capture.

The server responds with one JSON line such as `{"ok": true, "message": "Context inserted."}` or an error with `ok: false`, then closes. Selection updates acknowledge with `Editor location updated.` Wait for the acknowledgement before retrying an explicit capture. A connection lost before acknowledgement has an uncertain outcome: check the scratchpad first to avoid duplicates. There is also an internal `{"version":1,"type":"show"}` message for repeated app launches.

Try the example against a running app:

```powershell
.\.venv\Scripts\python.exe tools\send_context.py examples\editor-context.json
```

`--no-hotkeys` disables global capture. `--no-ipc` disables the bridge. Both leave the editor usable.

## Architecture and validation

`context.py` holds the capture contract and Markdown formatting. `capture.py` handles direct selection and the bounded clipboard transaction through a desktop adapter. `windows.py` owns UI Automation, Win32 clipboard memory, and app-aware copy chords. `hotkeys.py` registers native global shortcuts. `editor.py` holds the canonical Markdown text and tracked insertions. `composer.py` synchronizes the offline CodeMirror component with that document, with revision checks to prevent stale web events overwriting a capture. `app.py` runs the GUI, tray, lifecycle, and draft persistence. `ipc.py` is the extension seam.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tools\verify_composer.py
.\.venv\Scripts\python.exe tools\render_preview.py --native
```

Unit tests cover capture failures and clipboard conflicts through fake adapters; native Win32 memory handling and input ABI without touching the system clipboard; real Qt caret insertion, Unicode, capture undo, full-document copy, persistence, capture mode, and local IPC framing. The real WebEngine verification exercises toolbar edits, mode switching without text rewriting, typing, caret synchronization, capture undo, and Ctrl+Enter.

`tools/verify_windows_capture.py` is a visible integration check with isolated native-editor and Chromium browser fixtures. It tests direct accessibility selection, a real Ctrl+Alt+A keypress through native registration, and actual copy shortcuts with clipboard restoration. **Quit the regular scratchpad before running it**, so the test can register the shortcut. This uses only fixture text; it does not select or type in your existing applications. Specific Cursor, VS Code, Codex, and browser configurations still need user verification.

The local editor bundle is included in the Python package; Node is needed only to change it:

```powershell
cd web
npm ci
npm run build
```

The packaged component loads local assets only, blocks remote navigation and requests, and uses an off-the-record WebEngine profile. It never loads CDN scripts or sends prompt text over the network.

To reduce background RAM, the formatted editor releases its Chromium renderer after the window has been hidden in the tray for 30 seconds. Reopening reloads the local editor and restores the document, selection, undo/redo history, editing mode, and scroll position. Captures and Copy Prompt continue to use the canonical Qt document. The visible editor still needs Chromium's normal memory footprint.
