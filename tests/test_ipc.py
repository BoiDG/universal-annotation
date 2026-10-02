import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import json
import time
import unittest
from uuid import uuid4

from PySide6.QtNetwork import QLocalSocket
from PySide6.QtWidgets import QApplication

from prompt_scratchpad.ipc import CaptureServer

app = QApplication.instance() or QApplication([])


class IpcTests(unittest.TestCase):
    def setUp(self):
        self.received = []
        self.selections = []
        self.server = CaptureServer(self.received.append, selection=self.selections.append)
        self.name = "scratchpad-test-" + uuid4().hex
        self.assertTrue(self.server.server.listen(self.name))

    def tearDown(self):
        self.server.close()
        self.server.deleteLater()
        app.processEvents()

    def exchange(self, payload, split=False):
        socket = QLocalSocket()
        socket.connectToServer(self.name)
        self.assertTrue(socket.waitForConnected(1000))
        if split:
            socket.write(payload[:8])
            socket.flush()
            app.processEvents()
            self.assertEqual(self.received, [])
            socket.write(payload[8:])
        else:
            socket.write(payload)
        socket.flush()
        response = bytearray()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            app.processEvents()
            response.extend(bytes(socket.readAll()))
            if b"\n" in response:
                break
            time.sleep(0.005)
        socket.abort()
        self.assertIn(b"\n", response)
        return json.loads(response)

    def test_fragmented_capture_inserted_once(self):
        payload = json.dumps({"version": 1, "type": "capture", "text": "unicode α\nline two", "language": "python"}).encode() + b"\n"
        self.assertTrue(self.exchange(payload, split=True)["ok"])
        self.assertEqual(len(self.received), 1)
        self.assertEqual(self.received[0].text, "unicode α\nline two")

    def test_malformed_and_multiple_messages_do_not_insert(self):
        for payload in (b"not json\n", b'{"version":1,"type":"capture","text":""}\n', b'{}\n{}\n', b'\xff\n'):
            self.assertFalse(self.exchange(payload)["ok"])
        self.assertEqual(self.received, [])

    def test_selection_metadata_updates_without_inserting_context(self):
        message = {"version": 1, "type": "selection", "active": True, "application": "Cursor", "file_path": "C:/repo/main.py", "start_line": 2, "end_line": 3}
        self.assertTrue(self.exchange(json.dumps(message).encode() + b"\n")["ok"])
        self.assertEqual(self.received, [])
        self.assertEqual(self.selections[0].file_path, "C:/repo/main.py")
        self.assertTrue(self.exchange(b'{"version":1,"type":"selection","active":false}\n')["ok"])
        self.assertIsNone(self.selections[-1])
