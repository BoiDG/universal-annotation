"""Bounded clipboard transaction; no background selection polling."""

import time
import uuid
from typing import Protocol, Any

from .context import Context


class CaptureError(RuntimeError):
    pass


class Desktop(Protocol):
    def wait_for_hotkey_release(self) -> None: ...
    def foreground(self) -> int: ...
    def describe(self, window: int) -> tuple[str, str, str]: ...
    def snapshot(self) -> Any: ...
    def sentinel(self, text: str) -> int: ...
    def sequence(self) -> int: ...
    def copy_selection(self, window: int, window_class: str) -> None: ...
    def read_text(self) -> tuple[int, str | None]: ...
    def restore(self, snapshot: Any, expected_sequence: int) -> bool: ...


def capture_selection(desktop: Desktop, window: int, timeout: float = 1.5) -> Context:
    desktop.wait_for_hotkey_release()
    if not window or desktop.foreground() != window:
        raise CaptureError("Focus changed before capture. Select text and try again.")
    application, title, window_class = desktop.describe(window)
    read_selection = getattr(desktop, "read_selection", None)
    if read_selection:
        text = read_selection(window)
        if desktop.foreground() != window:
            raise CaptureError("Focus changed before capture. Select text and try again.")
        if text and text.strip():
            return Context(text=text, application=application, window=title)
    snapshot = desktop.snapshot()  # Must succeed before changing the clipboard.
    marker = f"PromptScratchpad:{uuid.uuid4()}"
    expected_sequence = desktop.sentinel(marker)
    try:
        desktop.copy_selection(window, window_class)
        deadline = time.monotonic() + timeout
        copied_empty = False
        while time.monotonic() < deadline:
            if desktop.foreground() != window:
                raise CaptureError("Focus changed during capture. Try again.")
            if desktop.sequence() != expected_sequence:
                sequence, text = desktop.read_text()
                expected_sequence = sequence
                if text and text != marker and text.strip():
                    return Context(text=text, application=application, window=title)
                # EmptyClipboard and SetClipboardData can happen in separate
                # message-loop turns. Wait for the finished copy, not its start.
                copied_empty = True
            time.sleep(0.02)
        if copied_empty:
            raise CaptureError("The app copied no usable text. Select text and try again.")
        raise CaptureError("No selection was copied. Select text first, or copy it and use Capture Clipboard.")
    finally:
        if not desktop.restore(snapshot, expected_sequence):
            raise CaptureError("Clipboard changed during capture; it was left untouched. Try again.")
