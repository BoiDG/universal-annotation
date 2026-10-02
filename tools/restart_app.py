"""Save and gracefully exit our existing Qt instance before relaunching."""

from pathlib import Path
import time

import win32api
import win32con
import win32gui
import win32process
from PySide6.QtCore import QStandardPaths
from PySide6.QtWidgets import QApplication

app = QApplication([])
app.setOrganizationName("PromptScratchpad")
app.setApplicationName("Prompt Scratchpad")
state = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation))
windows = []
win32gui.EnumWindows(lambda hwnd, _: windows.append(hwnd) if win32gui.GetWindowText(hwnd) == "Prompt Scratchpad" else None, None)
for hwnd in windows:
    thread, pid = win32process.GetWindowThreadProcessId(hwnd)
    paths = (state / "draft.json", state / "draft-recovered.json")
    previous = {path: path.stat().st_mtime_ns if path.exists() else 0 for path in paths}
    # WM_CLOSE invokes the app's synchronous save and hides its tray window.
    win32gui.SendMessageTimeout(hwnd, win32con.WM_CLOSE, 0, 0, win32con.SMTO_ABORTIFHUNG, 3000)
    deadline = time.monotonic() + 3
    while not any(path.exists() and path.stat().st_mtime_ns > previous[path] for path in paths) and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)
    if not any(path.exists() and path.stat().st_mtime_ns > previous[path] for path in paths):
        raise RuntimeError("Draft save could not be confirmed; the existing app was left running.")
    win32api.PostThreadMessage(thread, win32con.WM_QUIT, 0, 0)
    deadline = time.monotonic() + 5
    while win32gui.IsWindow(hwnd) and time.monotonic() < deadline:
        time.sleep(0.05)
    if win32gui.IsWindow(hwnd):
        raise RuntimeError("The saved app did not exit gracefully.")
    print(f"Saved draft and closed scratchpad instance {pid}.")
