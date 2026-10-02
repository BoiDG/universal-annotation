"""Visible, isolated native-editor/web-view fixtures for the real capture path."""

import os
os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--force-renderer-accessibility")

from concurrent.futures import ThreadPoolExecutor
import time

from PySide6.QtCore import Qt
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QPlainTextEdit
from pynput import keyboard
import win32gui

from prompt_scratchpad.capture import capture_selection
from prompt_scratchpad.hotkeys import GlobalHotkeys
from prompt_scratchpad.windows import WindowsDesktop

app = QApplication([])
pool = ThreadPoolExecutor(max_workers=1)


def pump(seconds=0.1):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def result(future, timeout=6):
    deadline = time.monotonic() + timeout
    while not future.done() and time.monotonic() < deadline:
        pump(0.02)
    assert future.done(), "Native capture did not finish within its deadline"
    return future.result()


def focus(widget):
    widget.show()
    widget.raise_()
    widget.activateWindow()
    widget.setFocus()
    pump(0.3)
    if win32gui.GetForegroundWindow() != int(widget.winId()):
        win32gui.SetForegroundWindow(int(widget.winId()))
    pump(0.1)


native = QPlainTextEdit()
native.setWindowTitle("Prompt Scratchpad — isolated capture fixture")
native.resize(620, 200)
expected = "Selected code α 😀\nsecond line"
native.setPlainText(expected)
focus(native)
native.selectAll()
desktop = WindowsDesktop(int(native.winId()))
before = desktop.sequence()
selected = result(pool.submit(desktop.read_selection, int(native.winId())))
assert selected == expected, f"Native accessibility selection mismatch: {selected!r}"
assert desktop.sequence() == before, "Direct selection changed clipboard"
print("PASS: native editor selection through UI Automation; clipboard untouched")

# Exercise the real global hook with the actual Ctrl+Alt+A key sequence.
captures = []
failures = []
listener = GlobalHotkeys(lambda: captures.append(pool.submit(capture_selection, desktop, desktop.foreground())), lambda: None, lambda: None, failures.append)
listener.start()
listener.wait()
controller = keyboard.Controller()
for key in (keyboard.Key.ctrl, keyboard.Key.alt, "a"):
    controller.press(key)
for key in ("a", keyboard.Key.alt, keyboard.Key.ctrl):
    controller.release(key)
deadline = time.monotonic() + 3
while not captures and time.monotonic() < deadline:
    pump()
listener.stop()
assert captures and not failures, (captures, failures)
assert result(captures[0]).text == expected
print("PASS: actual Ctrl+Alt+A global hook captures the native editor selection")

# Force the clipboard path so native Ctrl+Insert and restoration are tested too.
desktop.read_selection = lambda window: None
sequence, previous_text = desktop.read_text()
context = result(pool.submit(capture_selection, desktop, int(native.winId())))
assert context.text.replace("\r\n", "\n") == expected
assert desktop.read_text()[1] == previous_text
print("PASS: native Ctrl+Insert clipboard copy and restoration")

native.hide()
web = QWebEngineView()
web.setWindowTitle("Prompt Scratchpad — isolated browser fixture")
web.resize(620, 260)
loaded = []
web.loadFinished.connect(loaded.append)
web.setHtml("<!doctype html><html><body tabindex='0'><p id='selection'>Browser selection α</p></body></html>")
focus(web)
deadline = time.monotonic() + 5
while not loaded and time.monotonic() < deadline:
    pump()
assert loaded == [True]
selected_done = []
web.page().runJavaScript("document.body.focus(); const r=document.createRange(); r.selectNodeContents(document.getElementById('selection')); const s=window.getSelection(); s.removeAllRanges(); s.addRange(r); s.toString()", selected_done.append)
deadline = time.monotonic() + 3
while not selected_done and time.monotonic() < deadline:
    pump()
assert selected_done == ["Browser selection α"]

class BrowserDesktop(WindowsDesktop):
    def describe(self, window):
        # The fixture runs in python.exe; use Codex's Copy binding to exercise
        # the Chromium fallback used by the installed desktop/browser apps.
        _, title, window_class = super().describe(window)
        return "Codex", title, window_class

browser = BrowserDesktop(int(web.winId()))
browser.read_selection = lambda window: None
previous_text = browser.read_text()[1]
context = result(pool.submit(capture_selection, browser, int(web.winId())))
assert context.text == "Browser selection α", repr(context.text)
assert browser.read_text()[1] == previous_text
print("PASS: Chromium browser/app selection with Ctrl+C and clipboard restoration")
web.hide()
pool.shutdown(wait=True)
print("PASS: live Windows capture checks completed")
