"""Windows adapter. Snapshot HGLOBAL formats before synthetic copy."""

import ctypes
from ctypes import wintypes
from pathlib import PureWindowsPath
import time

import win32clipboard
import win32con
import win32gui
import win32process

from .capture import CaptureError

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
kernel32.GlobalSize.argtypes = [wintypes.HANDLE]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HANDLE
kernel32.GlobalFree.argtypes = [wintypes.HANDLE]
kernel32.GlobalFree.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", wintypes.DWORD), ("data", INPUTUNION)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT


class WindowsDesktop:
    def __init__(self, owner: int):
        self.owner = owner
        self._snapshot_sequence = None
        self._backup = None

    def foreground(self) -> int:
        return win32gui.GetForegroundWindow()

    def describe(self, window: int) -> tuple[str, str, str]:
        title = win32gui.GetWindowText(window)
        window_class = win32gui.GetClassName(window)
        _, pid = win32process.GetWindowThreadProcessId(window)
        process = kernel32.OpenProcess(0x1000, False, pid)
        name = ""
        if process:
            try:
                buffer = ctypes.create_unicode_buffer(32768)
                size = wintypes.DWORD(len(buffer))
                if kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
                    name = PureWindowsPath(buffer.value).stem
            finally:
                kernel32.CloseHandle(process)
        return name or window_class, title, window_class

    def wait_for_hotkey_release(self) -> None:
        deadline = time.monotonic() + 1.2
        keys = (win32con.VK_CONTROL, win32con.VK_MENU, win32con.VK_SHIFT, win32con.VK_LWIN, win32con.VK_RWIN, ord("A"))
        while any(user32.GetAsyncKeyState(key) & 0x8000 for key in keys):
            if time.monotonic() >= deadline:
                raise CaptureError("Release the capture hotkey, then try again.")
            time.sleep(0.01)

    def read_selection(self, window: int) -> str | None:
        """Read selected ranges only, never a control's entire value/document."""
        import comtypes
        import comtypes.client
        initialized = False
        try:
            comtypes.CoInitializeEx()
            initialized = True
            types = comtypes.client.GetModule("UIAutomationCore.dll")
            client = comtypes.client.CreateObject("{E22AD333-B25F-460C-83D0-0581107395C9}", interface=types.IUIAutomation2)
            client.ConnectionTimeout = 400
            client.TransactionTimeout = 600
            root = client.ElementFromHandle(window)
            element = client.GetFocusedElement()
            walker = client.RawViewWalker
            text = None
            deadline = time.monotonic() + 1.0
            # Walk ancestors only, verifying that the selected range belongs to
            # the recorded source window. Each COM provider call is bounded.
            for _ in range(24):
                if not element or time.monotonic() >= deadline:
                    break
                if not text:
                    try:
                        pattern = element.GetCurrentPattern(10014).QueryInterface(types.IUIAutomationTextPattern)
                        ranges = pattern.GetSelection()
                        parts = [ranges.GetElement(index).GetText(-1) for index in range(ranges.Length)]
                        text = "\n".join(part for part in parts if part)
                    except Exception:
                        pass
                if client.CompareElements(element, root):
                    return text if text and text.strip() else None
                element = walker.GetParentElement(element)
        except Exception:
            # Unsupported providers use the bounded copy transaction instead.
            return None
        finally:
            if initialized:
                comtypes.CoUninitialize()
        return None

    def _open(self) -> None:
        deadline = time.monotonic() + 0.3
        while True:
            try:
                win32clipboard.OpenClipboard(self.owner)
                return
            except Exception:
                if time.monotonic() >= deadline:
                    raise CaptureError("Clipboard is busy. Try again.") from None
                time.sleep(0.01)

    def snapshot(self) -> list[tuple[int, bytes]]:
        self._backup = None
        self._snapshot_sequence = None
        self._open()
        try:
            formats = []
            current = 0
            while current := win32clipboard.EnumClipboardFormats(current):
                formats.append(current)
            # Windows synthesizes CF_BITMAP from DIB/DIBV5. Back up the actual
            # pixel buffer, not that synthesized GDI handle.
            if 8 in formats or 17 in formats:
                formats = [fmt for fmt in formats if fmt != 2]
            # These handles contain GDI objects or nested pointers, not raw bytes.
            unsafe = {2, 3, 9, 14, 0x80, 0x82, 0x83, 0x8E}
            if unsafe.intersection(formats):
                raise CaptureError("Clipboard contains an image/object that cannot be safely restored. Use Capture Clipboard or copy text first.")
            result = []
            total = 0
            for fmt in formats:
                handle = user32.GetClipboardData(fmt)
                size = kernel32.GlobalSize(handle) if handle else 0
                total += size
                if not handle or not size or total > 32 * 1024 * 1024:
                    raise CaptureError("Clipboard cannot be safely backed up. Copy text first and use Capture Clipboard.")
                pointer = kernel32.GlobalLock(handle)
                if not pointer:
                    raise CaptureError("Could not back up clipboard contents.")
                try:
                    result.append((fmt, ctypes.string_at(pointer, size)))
                finally:
                    kernel32.GlobalUnlock(handle)
            self._snapshot_sequence = self.sequence()
            self._backup = result
            return result
        finally:
            win32clipboard.CloseClipboard()

    def _replace_locked(self, formats: list[tuple[int, bytes]]) -> None:
        allocated = []
        try:
            for fmt, value in formats:
                handle = kernel32.GlobalAlloc(0x0002, len(value))
                if not handle:
                    raise CaptureError("Could not allocate clipboard backup.")
                allocated.append([fmt, handle])
                pointer = kernel32.GlobalLock(handle)
                if not pointer:
                    raise CaptureError("Could not lock clipboard backup.")
                try:
                    ctypes.memmove(pointer, value, len(value))
                finally:
                    kernel32.GlobalUnlock(handle)
            win32clipboard.EmptyClipboard()
            for entry in allocated:
                if not user32.SetClipboardData(entry[0], entry[1]):
                    raise CaptureError("Could not restore all clipboard formats.")
                entry[1] = None  # Ownership transferred to Windows.
        finally:
            for _, handle in allocated:
                if handle:
                    kernel32.GlobalFree(handle)

    def sentinel(self, text: str) -> int:
        backup, snapshot_sequence = self._backup, self._snapshot_sequence
        # The local copy is needed only if writing the sentinel fails. Do not
        # retain up to 32 MB of clipboard pixels for the lifetime of the app.
        self._backup = None
        self._snapshot_sequence = None
        self._open()
        try:
            if self.sequence() != snapshot_sequence:
                raise CaptureError("Clipboard changed before capture. Nothing was overwritten; try again.")
            formats = [(win32con.CF_UNICODETEXT, (text + "\0").encode("utf-16-le"))]
            for name in ("CanIncludeInClipboardHistory", "CanUploadToCloudClipboard"):
                formats.append((win32clipboard.RegisterClipboardFormat(name), b"\0\0\0\0"))
            try:
                self._replace_locked(formats)
            except Exception:
                self._replace_locked(backup)
                raise
            sequence = self.sequence()
        finally:
            win32clipboard.CloseClipboard()
        return sequence

    def sequence(self) -> int:
        return win32clipboard.GetClipboardSequenceNumber()

    def read_text(self) -> tuple[int, str | None]:
        self._open()
        try:
            text = win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT) if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT) else None
            return self.sequence(), text
        finally:
            win32clipboard.CloseClipboard()

    def restore(self, snapshot: list[tuple[int, bytes]], expected_sequence: int) -> bool:
        self._open()
        try:
            if self.sequence() != expected_sequence:
                return False
            self._replace_locked(snapshot)
            return True
        finally:
            win32clipboard.CloseClipboard()

    def copy_selection(self, window: int, window_class: str) -> None:
        if self.foreground() != window:
            raise CaptureError("Focus changed. Capture cancelled.")
        # Chromium desktop apps do not consistently implement Ctrl+Insert.
        # Use their Copy binding, while shell/editor terminals retain a safe
        # chord. Accessibility capture above handles supported editor controls.
        keys = [win32con.VK_CONTROL, win32con.VK_INSERT]
        if window_class == "CASCADIA_HOSTING_WINDOW_CLASS":
            keys = [win32con.VK_CONTROL, win32con.VK_SHIFT, ord("C")]
        else:
            application, _, _ = self.describe(window)
            if application.lower() in {"codex", "chrome", "msedge", "firefox", "brave", "opera", "vivaldi", "notepad", "notepad++"}:
                keys = [win32con.VK_CONTROL, ord("C")]
        events = [INPUT(type=1, ki=KEYBDINPUT(wVk=key)) for key in keys]
        events += [INPUT(type=1, ki=KEYBDINPUT(wVk=key, dwFlags=win32con.KEYEVENTF_KEYUP)) for key in reversed(keys)]
        inputs = (INPUT * len(events))(*events)
        sent = user32.SendInput(len(events), inputs, ctypes.sizeof(INPUT))
        if sent != len(events):
            # Release synthetic keys if Windows accepted only part of the batch.
            releases = (INPUT * len(keys))(*(INPUT(type=1, ki=KEYBDINPUT(wVk=key, dwFlags=win32con.KEYEVENTF_KEYUP)) for key in reversed(keys)))
            user32.SendInput(len(keys), releases, ctypes.sizeof(INPUT))
            raise CaptureError("Windows blocked the copy shortcut. Copy manually and use Capture Clipboard (elevated apps may require this).")
