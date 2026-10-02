"""Raw Markdown editor with tracked, reversible capture insertions."""

from dataclasses import dataclass
import re

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit

from .context import Context, format_context


def selected_text(cursor: QTextCursor) -> str:
    return cursor.selectedText().replace("\u2029", "\n").replace("\u2028", "\n")


@dataclass
class CaptureSpan:
    start: QTextCursor
    end: QTextCursor
    original: str

    def cursor(self) -> QTextCursor:
        result = QTextCursor(self.start)
        result.setPosition(self.end.position(), QTextCursor.MoveMode.KeepAnchor)
        return result


class MarkdownHighlight(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.heading = QTextCharFormat()
        self.heading.setForeground(QColor("#8dbdff"))
        self.heading.setFontWeight(QFont.Weight.Bold)
        self.code = QTextCharFormat()
        self.code.setForeground(QColor("#b5d6b2"))
        self.fence = QTextCharFormat()
        self.fence.setForeground(QColor("#e7ba7e"))

    def highlightBlock(self, text):
        previous = max(0, self.previousBlockState())
        fence = re.match(r"^\s*(`{3,})(.*)$", text)
        if previous:
            self.setFormat(0, len(text), self.code)
            closes = fence and len(fence[1]) >= previous and not fence[2].strip()
            self.setCurrentBlockState(0 if closes else previous)
            if closes:
                self.setFormat(0, len(text), self.fence)
        elif fence:
            self.setFormat(0, len(text), self.fence)
            self.setCurrentBlockState(len(fence[1]))
        else:
            self.setCurrentBlockState(0)
            if re.match(r"^#{1,6} ", text):
                self.setFormat(0, len(text), self.heading)


class PromptEditor(QPlainTextEdit):
    capture_count_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.spans: list[CaptureSpan] = []
        self.setPlaceholderText("Write your instructions here…\n\nSelect text in another app and press Ctrl+Alt+A to insert context at the caret.")
        self.setFont(QFont("Cascadia Code", 11))
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self.highlighter = MarkdownHighlight(self.document())
        self.textChanged.connect(self._changed)

    def _changed(self):
        self.spans = [span for span in self.spans if span.end.position() > span.start.position()]
        self.capture_count_changed.emit(len(self.spans))

    def insert_context(self, context: Context):
        block = format_context(context).replace("\r\n", "\n").replace("\r", "\n")
        cursor = self.textCursor()
        # Insert at the active caret, preserving any text selected in this editor.
        cursor.clearSelection()
        before = QTextCursor(cursor)
        before.movePosition(QTextCursor.MoveOperation.Start, QTextCursor.MoveMode.KeepAnchor)
        preceding = selected_text(before)
        prefix = "" if not preceding or preceding.endswith("\n\n") else "\n" if preceding.endswith("\n") else "\n\n"
        suffix = "\n"
        content = prefix + block + suffix
        start_position = cursor.position()
        cursor.beginEditBlock()
        cursor.insertText(content)
        cursor.endEditBlock()
        end_position = cursor.position()
        self.setTextCursor(cursor)
        self.spans.append(self._span(start_position, end_position, content))
        self.capture_count_changed.emit(len(self.spans))

    def _span(self, start, end, content):
        left = QTextCursor(self.document())
        left.setPosition(start)
        left.setKeepPositionOnInsert(False)
        right = QTextCursor(self.document())
        right.setPosition(end)
        right.setKeepPositionOnInsert(True)
        return CaptureSpan(left, right, content)

    def undo_capture(self) -> bool:
        if not self.spans:
            raise ValueError("No capture to undo.")
        span = self.spans[-1]
        cursor = span.cursor()
        if selected_text(cursor) != span.original:
            raise ValueError("This capture was edited. Use Ctrl+Z or remove its text manually to preserve your changes.")
        self.spans.pop()
        cursor.beginEditBlock()
        cursor.removeSelectedText()
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        self.capture_count_changed.emit(len(self.spans))
        return True

    def reset(self):
        self.spans.clear()
        cursor = self.textCursor()
        cursor.beginEditBlock()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.removeSelectedText()
        cursor.endEditBlock()
        self.setTextCursor(cursor)
        self.capture_count_changed.emit(0)

    def capture_records(self) -> list[dict]:
        return [{"start": span.start.position(), "end": span.end.position(), "original": span.original} for span in self.spans]

    def restore_records(self, records):
        self.spans.clear()
        if isinstance(records, list):
            limit = self.document().characterCount() - 1
            for entry in records:
                if not isinstance(entry, dict):
                    continue
                start, end, content = entry.get("start"), entry.get("end"), entry.get("original")
                if type(start) is int and type(end) is int and 0 <= start < end <= limit and isinstance(content, str):
                    self.spans.append(self._span(start, end, content))
        self.capture_count_changed.emit(len(self.spans))
