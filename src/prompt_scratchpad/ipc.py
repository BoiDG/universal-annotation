"""One newline-delimited JSON capture per same-user local connection."""

import json

from PySide6.QtCore import QObject, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .context import context_from_message, selection_from_message

SERVER_NAME = "prompt-scratchpad-v1"
MAX_MESSAGE_BYTES = 1_100_000


class CaptureServer(QObject):
    def __init__(self, receive, parent=None, show=None, selection=None):
        super().__init__(parent)
        self.receive = receive
        self.show = show
        self.selection = selection
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.server.setMaxPendingConnections(8)
        self.server.newConnection.connect(self._accept)
        self.clients = {}

    def start(self) -> bool:
        return self.server.listen(SERVER_NAME)

    def close(self):
        self.server.close()
        for socket in list(self.clients):
            socket.abort()

    def _accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if len(self.clients) >= 8:
                socket.abort()
                socket.deleteLater()
                continue
            socket.setReadBufferSize(MAX_MESSAGE_BYTES + 1)
            timer = QTimer(socket)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda s=socket: self._reply(s, False, "Request timed out."))
            timer.start(5000)
            self.clients[socket] = (bytearray(), timer)
            socket.readyRead.connect(lambda s=socket: self._read(s))
            socket.disconnected.connect(lambda s=socket: self._forget(s))
            self._read(socket)

    def _forget(self, socket):
        self.clients.pop(socket, None)
        socket.deleteLater()

    def _read(self, socket):
        if socket not in self.clients:
            return
        buffer, _ = self.clients[socket]
        buffer.extend(bytes(socket.readAll()))
        if len(buffer) > MAX_MESSAGE_BYTES:
            self._reply(socket, False, "Request exceeds 1.1 MB.")
            return
        if b"\n" not in buffer:
            return
        line, rest = bytes(buffer).split(b"\n", 1)
        if rest.strip():
            self._reply(socket, False, "Send only one capture per connection.")
            return
        try:
            message = json.loads(line.decode("utf-8"))
            if message == {"version": 1, "type": "show"} and self.show:
                self.show()
                reply = "Scratchpad shown."
            elif isinstance(message, dict) and message.get("type") == "selection" and self.selection:
                self.selection(selection_from_message(message))
                reply = "Editor location updated."
            else:
                context = context_from_message(message)
                self.receive(context)
                reply = "Context inserted."
        except (ValueError, RuntimeError, UnicodeError) as error:
            self._reply(socket, False, str(error))
        else:
            self._reply(socket, True, reply)

    def _reply(self, socket, ok, message):
        entry = self.clients.pop(socket, None)
        if entry is None:
            return
        entry[1].stop()
        socket.write(json.dumps({"ok": ok, "message": message}).encode("utf-8") + b"\n")
        socket.disconnectFromServer()
