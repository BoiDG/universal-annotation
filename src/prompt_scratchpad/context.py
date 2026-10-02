"""UI-independent capture contract shared by Windows capture and editor IPC."""

from dataclasses import dataclass
import re

MAX_TEXT_BYTES = 1_000_000


@dataclass(frozen=True)
class Context:
    text: str
    application: str = ""
    window: str = ""
    file_path: str = ""
    language: str = "text"
    workspace: str = ""
    start_line: int | None = None
    end_line: int | None = None
    reference_only: bool = False


@dataclass(frozen=True)
class EditorSelection:
    application: str
    file_path: str
    start_line: int
    end_line: int
    workspace: str = ""


def _label(value: str) -> str:
    # Metadata must never break out into Markdown instructions/headings.
    value = " ".join(value.split())
    return re.sub(r"([\\`*_{}\[\]<>#|])", r"\\\1", value)


def format_context(context: Context) -> str:
    """Wrap verbatim text in a fence longer than any embedded backtick run."""
    if context.reference_only:
        if not context.file_path or not context.start_line or not context.end_line:
            raise ValueError("An IDE reference needs a file path and line range.")
        if context.end_line < context.start_line:
            raise ValueError("The ending line must be at least the starting line.")
        source = _label(context.application or "Editor")
        return f"### Code reference — {source}\n\nFile: {_label(context.file_path.replace(chr(92), '/'))}\nLines: {context.start_line}–{context.end_line}\n"
    if not context.text.strip():
        raise ValueError("No selected text was captured.")
    if len(context.text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ValueError("Selection exceeds the 1 MB capture limit.")
    source = context.application or context.window or "Selection"
    if context.application and context.window:
        source = f"{context.application} — {context.window}"
    heading = f"### Context — {_label(source)}\n\n"
    metadata = []
    if context.file_path:
        metadata.append(f"File: {_label(context.file_path)}")
    if context.start_line is not None:
        end = context.end_line or context.start_line
        metadata.append(f"Lines: {context.start_line}–{end}")
    if context.workspace:
        metadata.append(f"Workspace: {_label(context.workspace)}")
    if metadata:
        heading += "\n".join(metadata) + "\n\n"
    runs = [len(match.group()) for match in re.finditer(r"`+", context.text)]
    fence = "`" * max(3, max(runs, default=0) + 1)
    language = context.language if re.fullmatch(r"[\w#+.-]{1,40}", context.language) else "text"
    # The only extra newline is outside the captured text, to close the fence.
    newline = "" if context.text.endswith("\n") else "\n"
    return f"{heading}{fence}{language}\n{context.text}{newline}{fence}\n"


def selection_from_message(message: object) -> EditorSelection | None:
    if not isinstance(message, dict) or message.get("version") != 1 or message.get("type") != "selection":
        raise ValueError("Expected a version 1 selection message.")
    if set(message) - {"version", "type", "active", "application", "file_path", "start_line", "end_line", "workspace"}:
        raise ValueError("Unknown selection fields.")
    if message.get("active") is False:
        return None
    if message.get("active") is not True:
        raise ValueError("active must be a boolean.")
    application, path = message.get("application"), message.get("file_path")
    if not isinstance(application, str) or not application or len(application) > 100:
        raise ValueError("application must be a short string.")
    if not isinstance(path, str) or not path or len(path) > 4096 or "\n" in path or "\r" in path:
        raise ValueError("file_path must be a file path.")
    workspace = message.get("workspace", "")
    if not isinstance(workspace, str) or len(workspace) > 4096 or "\n" in workspace or "\r" in workspace:
        raise ValueError("workspace must be a short string.")
    start, end = message.get("start_line"), message.get("end_line")
    if type(start) is not int or type(end) is not int or start < 1 or end < start:
        raise ValueError("Provide a valid one-based line range.")
    return EditorSelection(application, path, start, end, workspace)


def context_from_message(message: object) -> Context:
    if not isinstance(message, dict) or message.get("version") != 1:
        raise ValueError("Expected a version 1 JSON object.")
    if message.get("type") != "capture":
        raise ValueError("Expected type 'capture'.")
    allowed = {"version", "type", "text", "application", "window", "file_path", "language", "workspace", "start_line", "end_line"}
    if set(message) - allowed:
        raise ValueError("Unknown capture fields.")
    fields = {key: value for key, value in message.items() if key not in {"version", "type"}}
    if not isinstance(fields.get("text"), str):
        raise ValueError("text must be a string.")
    for name in {"application", "window", "file_path", "language", "workspace"}:
        if name in fields and (not isinstance(fields[name], str) or len(fields[name]) > 4096):
            raise ValueError(f"{name} must be a string of at most 4096 characters.")
    for name in {"start_line", "end_line"}:
        value = fields.get(name)
        if value is not None and (type(value) is not int or value < 1):
            raise ValueError(f"{name} must be a positive integer.")
    if fields.get("end_line") is not None and fields.get("start_line") is None:
        raise ValueError("end_line requires start_line.")
    if fields.get("end_line") is not None and fields["end_line"] < fields["start_line"]:
        raise ValueError("end_line must be at least start_line.")
    result = Context(**fields)
    format_context(result)
    return result
