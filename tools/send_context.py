"""Example IPC client; a future VS Code extension sends the same JSON."""

import argparse
import json
from pathlib import Path
import sys
import time

from PySide6.QtCore import QCoreApplication
from PySide6.QtNetwork import QLocalSocket

from prompt_scratchpad.context import context_from_message
from prompt_scratchpad.ipc import SERVER_NAME


def main():
    parser = argparse.ArgumentParser(description="Send a context JSON file to a running Prompt Scratchpad")
    parser.add_argument("json_file", type=Path)
    options = parser.parse_args()
    try:
        message = json.loads(options.json_file.read_text(encoding="utf-8"))
        context_from_message(message)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    app = QCoreApplication(sys.argv[:1])
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if not socket.waitForConnected(1000):
        print("Prompt Scratchpad is not running or its bridge is unavailable.", file=sys.stderr)
        return 1
    socket.write(json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n")
    socket.waitForBytesWritten(1000)
    response = bytearray()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        app.processEvents()
        response.extend(bytes(socket.readAll()))
        if b"\n" in response:
            break
        socket.waitForReadyRead(50)
    socket.disconnectFromServer()
    if b"\n" not in response:
        print("No acknowledgement received. Check the scratchpad before retrying; a context may have been inserted.", file=sys.stderr)
        return 1
    acknowledgement = json.loads(response.split(b"\n", 1)[0])
    print(acknowledgement["message"])
    return 0 if acknowledgement["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
