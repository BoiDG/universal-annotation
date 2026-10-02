"""Render the real Qt window offscreen without touching live clipboard/apps."""

import os
import sys
if "--native" not in sys.argv:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --disable-gpu-compositing")

from pathlib import Path
import tempfile
import time

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFontDatabase

from prompt_scratchpad.app import ScratchpadWindow
from prompt_scratchpad.context import Context

app = QApplication([])
if os.name == "nt":
    # Qt's offscreen plugin does not discover installed Windows fonts itself.
    for filename in ("segoeui.ttf", "segoeuib.ttf", "consola.ttf"):
        font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
output = Path(__file__).resolve().parents[1] / "artifacts" / "scratchpad-preview.png"
output.parent.mkdir(exist_ok=True)
with tempfile.TemporaryDirectory() as directory:
    window = ScratchpadWindow(Path(directory) / "draft.json", desktop_enabled=False, ipc_enabled=False)
    window.editor.setPlainText("Please fix the failing validation in this function.\nPreserve the public API and explain the change.\n\n")
    from PySide6.QtGui import QTextCursor
    window.editor.moveCursor(QTextCursor.MoveOperation.End)
    window.receive_context(Context("def validate_prompt(text: str) -> bool:\n    return bool(text)\n", application="Cursor", window="validation.py", file_path="C:/projects/agent/validation.py", language="python", start_line=12, end_line=13))
    window.editor.insertPlainText("Whitespace-only prompts should fail validation.\nAdd a focused regression test.")
    window.show()
    deadline = time.monotonic() + 15
    while not window.composer.ready and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    if not window.composer.ready:
        raise RuntimeError("Markdown component did not initialize.")
    for _ in range(50):
        app.processEvents()
        time.sleep(0.01)
    assert window.grab().save(str(output))
    window.autosave.stop()
    window.server.close()
    window.executor.shutdown(wait=True)
    window.composer.shutdown()
    window.hide()
print(output)
