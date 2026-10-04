"""Offline CodeMirror Markdown component backed by the canonical Qt document."""

import json
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings, QWebEngineUrlRequestInterceptor
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QVBoxLayout, QWidget

from .attachments import AttachmentPreviews


def utf16_size(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


class LocalResources(QWebEngineUrlRequestInterceptor):
    def __init__(self, attachment_directory, parent=None):
        super().__init__(parent)
        self.roots = (Path(__file__).parent.joinpath("assets").resolve(), Path(attachment_directory).resolve())

    def interceptRequest(self, info):
        url = info.requestUrl()
        if url.scheme() == "file":
            location = Path(url.toLocalFile()).resolve()
            if not any(location.is_relative_to(root) for root in self.roots):
                info.block(True)
        elif url.scheme() not in {"qrc", "about"}:
            info.block(True)


class EditorPage(QWebEnginePage):
    console_error = Signal(str)

    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        return url.scheme() == "file" and url.fileName() == "editor.html"

    def javaScriptConsoleMessage(self, level, message, line, source):
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            self.console_error.emit(f"Markdown editor: {message}")


class ComposerBridge(QObject):
    def __init__(self, composer):
        super().__init__(composer)
        self.composer = composer

    @Slot()
    def ready(self):
        self.composer.ready = True
        if self.composer.resume_state is not None:
            state = json.dumps(self.composer.resume_state)
            self.composer.page.runJavaScript(f"window.scratchpad.restoreState({state})")
            self.composer.resume_state = None
        self.composer.push_document()
        self.composer.loaded.emit()
        if not self.composer.isVisible():
            self.composer.idle_timer.start()

    @Slot(str, int, int, int)
    def stateChanged(self, text, anchor, head, revision):
        self.composer.accept_web_state(text, anchor, head, revision)

    @Slot()
    def copyPrompt(self):
        self.composer.copy_requested.emit()

    @Slot()
    def cancelCapture(self):
        self.composer.cancel_requested.emit()

    @Slot()
    def attachImageDialog(self):
        self.composer.image_dialog_requested.emit()

    @Slot()
    def attachClipboardImage(self):
        self.composer.clipboard_image_requested.emit()

    @Slot(str, result=str)
    def imagePreview(self, path):
        preview = self.composer.previews.path_for(path)
        return QUrl.fromLocalFile(str(preview)).toString() if preview else ""


class MarkdownComposer(QWidget):
    copy_requested = Signal()
    cancel_requested = Signal()
    loaded = Signal()
    error = Signal(str)
    image_dialog_requested = Signal()
    clipboard_image_requested = Signal()

    def __init__(self, source, attachment_directory, parent=None):
        super().__init__(parent)
        self.source = source
        self.previews = AttachmentPreviews(attachment_directory)
        self.ready = False
        self.applying = False
        self.revision = 0
        self.resume_state = None
        self.visibility_generation = 0
        self.stopping = False
        source.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.web = QWebEngineView(self)
        self.profile = QWebEngineProfile(self)
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.NoCache)
        self.interceptor = LocalResources(attachment_directory, self.profile)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        self.page = EditorPage(self.profile, self.web)
        self.page.setBackgroundColor(QColor("#1e1e1e"))
        self.page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        self.page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        self.page.console_error.connect(self.error)
        self.web.setPage(self.page)
        self.channel = QWebChannel(self.page)
        self.bridge = ComposerBridge(self)
        self.channel.registerObject("bridge", self.bridge)
        self.page.setWebChannel(self.channel)
        layout.addWidget(self.web)
        self.sync_timer = QTimer(self)
        self.sync_timer.setSingleShot(True)
        self.sync_timer.setInterval(0)
        self.sync_timer.timeout.connect(self.push_document)
        self.idle_timer = QTimer(self)
        self.idle_timer.setSingleShot(True)
        self.idle_timer.setInterval(30000)
        self.idle_timer.timeout.connect(self.suspend_editor)
        source.textChanged.connect(self._source_changed)
        source.cursorPositionChanged.connect(self._selection_changed)
        self.web.setUrl(QUrl.fromLocalFile(str(Path(__file__).parent / "assets" / "editor.html")))

    def _source_changed(self):
        if not self.applying:
            self.revision += 1
            self.sync_timer.start()

    def _selection_changed(self):
        if not self.applying:
            self.sync_timer.start()

    def push_document(self):
        if not self.ready:
            return
        cursor = self.source.textCursor()
        args = json.dumps([self.source.toPlainText(), cursor.anchor(), cursor.position(), self.revision])
        self.page.runJavaScript(f"window.scratchpad.setDocument(...{args})")

    def accept_web_state(self, text, anchor, head, revision):
        if revision != self.revision:
            self.sync_timer.start()
            return
        if not 0 <= anchor <= utf16_size(text) or not 0 <= head <= utf16_size(text):
            return
        self.applying = True
        try:
            old = self.source.toPlainText()
            if old != text:
                start, end, new_end = 0, len(old), len(text)
                while start < end and start < new_end and old[start] == text[start]:
                    start += 1
                while end > start and new_end > start and old[end - 1] == text[new_end - 1]:
                    end -= 1
                    new_end -= 1
                cursor = QTextCursor(self.source.document())
                cursor.setPosition(utf16_size(old[:start]))
                cursor.setPosition(utf16_size(old[:end]), QTextCursor.MoveMode.KeepAnchor)
                cursor.beginEditBlock()
                cursor.insertText(text[start:new_end])
                cursor.endEditBlock()
            cursor = self.source.textCursor()
            cursor.setPosition(anchor)
            cursor.setPosition(head, QTextCursor.MoveMode.KeepAnchor)
            self.source.setTextCursor(cursor)
        finally:
            self.applying = False

    def focus_editor(self):
        self.web.setFocus()
        if self.ready:
            self.page.runJavaScript("window.scratchpad.focus()")

    def hideEvent(self, event):
        super().hideEvent(event)
        self.visibility_generation += 1
        if self.ready and not self.stopping:
            self.idle_timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self.visibility_generation += 1
        self.idle_timer.stop()
        self.page.setLifecycleState(QWebEnginePage.LifecycleState.Active)

    def suspend_editor(self):
        if self.isVisible() or not self.ready or self.stopping:
            return
        generation = self.visibility_generation

        def saved(state):
            if self.stopping or self.isVisible() or generation != self.visibility_generation:
                return
            try:
                state = json.loads(state)
            except (TypeError, ValueError):
                self.idle_timer.start()
                return
            # Never discard an editor whose latest document could not be saved.
            if not isinstance(state, dict) or state.get("revision") != self.revision:
                self.idle_timer.start()
                return
            document = state.get("editor", {})
            if document.get("doc") != self.source.toPlainText():
                self.idle_timer.start()
                return
            self.resume_state = state
            self.ready = False
            self.sync_timer.stop()
            self.page.setLifecycleState(QWebEnginePage.LifecycleState.Discarded)

        self.page.runJavaScript("JSON.stringify(window.scratchpad.saveState())", saved)

    def shutdown(self):
        self.stopping = True
        self.visibility_generation += 1
        self.idle_timer.stop()
        self.resume_state = None
        self.sync_timer.stop()
        if self.ready:
            self.page.runJavaScript("window.scratchpad.shutdown()")
