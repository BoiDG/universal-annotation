"""Native Windows global hotkeys; registration failures are visible."""

import ctypes
from ctypes import wintypes
from threading import Event, Thread

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

CAPTURE = 1
ARM = 2
CANCEL = 3
WM_HOTKEY = 0x0312
WM_MODE = 0x8001
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000


def bindings():
    return ((CAPTURE, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("A")),
            (ARM, MOD_CONTROL | MOD_ALT | MOD_SHIFT | MOD_NOREPEAT, ord("A")))


class GlobalHotkeys:
    def __init__(self, capture, arm, cancel, failure):
        self.callbacks = {CAPTURE: capture, ARM: arm, CANCEL: cancel}
        self.failure = failure
        self.ready = Event()
        self.thread = Thread(target=self._run, name="windows-hotkeys", daemon=True)
        self.thread_id = 0
        self.error = ""

    def start(self):
        self.thread.start()
        if not self.ready.wait(2):
            raise RuntimeError("Windows hotkey registration timed out.")
        if self.error:
            raise RuntimeError(self.error)

    def wait(self):
        self.ready.wait(2)

    def stop(self):
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0)
            self.thread.join(timeout=1)

    def set_capture_mode(self, armed):
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, WM_MODE, int(armed), 0)

    def _run(self):
        registered = set()
        self.thread_id = kernel32.GetCurrentThreadId()
        message = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
        try:
            for identifier, modifiers, key in bindings():
                if not user32.RegisterHotKey(None, identifier, modifiers, key):
                    chord = "Ctrl+Alt+A" if identifier == CAPTURE else "Ctrl+Alt+Shift+A"
                    self.error = f"{chord} could not be registered (Windows error {ctypes.get_last_error()}). Another app may already use it."
                    return
                registered.add(identifier)
            self.ready.set()
            while True:
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    break
                if message.message == WM_HOTKEY:
                    callback = self.callbacks.get(message.wParam)
                    if callback:
                        try:
                            callback()
                        except Exception as error:
                            self.failure(str(error))
                elif message.message == WM_MODE:
                    if message.wParam and CANCEL not in registered:
                        if user32.RegisterHotKey(None, CANCEL, MOD_NOREPEAT, 0x1B):
                            registered.add(CANCEL)
                    elif not message.wParam and CANCEL in registered:
                        user32.UnregisterHotKey(None, CANCEL)
                        registered.remove(CANCEL)
        finally:
            for identifier in registered:
                user32.UnregisterHotKey(None, identifier)
            self.ready.set()
