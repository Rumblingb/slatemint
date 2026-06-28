#!/usr/bin/env python3
"""SlateMint MCP Server — Convert text between plain text, Markdown, HTML, and JSON payloads."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

RATE_LIMIT = 50
RATE_WINDOW = 60.0
PRO_KEY = "PROL_AGENTPAY_DEMO"
DEFAULT_STRIPE_LINK = "https://buy.stripe.com/test_placeholder"

STARTED_AT = datetime.now(timezone.utc).isoformat()


def log(msg: str) -> None:
    print(f"[SLATEMINT] {msg}", file=sys.stderr, flush=True)


class RateLimiter:
    def __init__(self) -> None:
        self._events: deque[float] = deque()

    def check(self) -> bool:
        now = time.time()
        while self._events and now - self._events[0] > RATE_WINDOW:
            self._events.popleft()
        if len(self._events) >= RATE_LIMIT:
            return False
        self._events.append(now)
        return True


rate_limiter = RateLimiter()


def encode_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=True, separators=(",", ":"))


def ok(result: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "result": result}


def error(err: str, code: str = "error") -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": err}}


def markdown_to_html(text: str) -> str:
    escaped = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    lines = escaped.splitlines()
    out: list[str] = []
    in_list = False
    for raw in lines:
        line = raw.rstrip()
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        bullet = re.match(r"^(\s*)[-*+]\s+(.+)$", line)
        numbered = re.match(r"^(\s*)\d+\.\s+(.+)$", line)
        if heading:
            if in_list:
                out.append("</ul>")
                in_list = False
            level = len(heading.group(1))
            out.append(f"<h{level}>{heading.group(2)}</h{level}>")
        elif bullet or numbered:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{(bullet or numbered).group(2)}</li>")
        else:
            if in_list:
                out.append("</ul>")
                in_list = False
            if line.strip() == "":
                out.append("<br />")
            else:
                out.append(f"<p>{line}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def clean_plain(text: str, preserve_lines: bool = False) -> str:
    text = re.sub(r"\r\n?", "\n", text)
    if not preserve_lines:
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def structured_to_markdown(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in payload.items():
        label = re.sub(r"[_-]+", " ", str(key)).strip().title()
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            value = json.dumps(value, indent=2, ensure_ascii=True)
        parts.append(f"## {label}\n\n{value}\n")
    return "\n".join(parts)


def markdown_to_json(text: str) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    current: dict[str, Any] | None = None
    current_list: list[str] = []
    current_key: str | None = None
    state = "root"
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        heading = re.match(r"^(#{2,6})\s+(.+)$", line)
        list_item = re.match(r"^(\s*)[-*+]\s+(.+)$", line)
        if heading:
            state = "heading"
            current_key = heading.group(2).strip()
            payload[current_key] = [] if False else ""
            current = None
            current_list = []
        elif list_item:
            state = "list"
            value = list_item.group(2).strip()
            if current_key is not None and isinstance(payload.get(current_key), list):
                payload[current_key].append(value)
            elif current_key is not None:
                payload[current_key] = [value]
            else:
                payload.setdefault("items", []).append(value)
        else:
            state = "text"
            text_value = line.strip()
            if text_value == "":
                continue
            if current_key is not None:
                existing = payload.get(current_key)
                if isinstance(existing, list):
                    existing.append(text_value)
                elif isinstance(existing, str) and existing:
                    payload[current_key] = existing + "\n" + text_value
                else:
                    payload[current_key] = text_value
            else:
                payload.setdefault("body", "")
                if payload["body"]:
                    payload["body"] += "\n" + text_value
                else:
                    payload["body"] = text_value
    return payload


def normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def summarize(text: str, max_sentences: int = 3) -> dict[str, Any]:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    summary = " ".join(sentences[:max_sentences])
    return {
        "summary": summary,
        "sentence_count": len(sentences),
        "word_count": len(text.split()),
        "character_count": len(text),
    }


def convert_text_markup(text: str, source_format: str, target_format: str) -> dict[str, Any]:
    source_format = source_format.lower()
    target_format = target_format.lower()
    if source_format not in {"plain", "markdown", "html", "json"}:
        return error(f"unsupported source format: {source_format}")
    if target_format not in {"plain", "markdown", "html", "json"}:
        return error(f"unsupported target format: {target_format}")
    normalized = clean_plain(text, preserve_lines=(source_format == target_format == "plain"))

    try:
        if source_format == target_format:
            converted = normalized
        elif source_format == "markdown" and target_format == "html":
            converted = markdown_to_html(normalized)
        elif source_format == "html" and target_format == "markdown":
            converted = f"# Converted HTML\n\n{normalized}"
        elif source_format == "json" and target_format == "markdown":
            payload = json.loads(normalized) if normalized else {}
            if not isinstance(payload, dict):
                payload = {"value": payload}
            converted = structured_to_markdown(payload)
        elif source_format in {"markdown", "plain"} and target_format == "json":
            converted = json.dumps(markdown_to_json(normalized if source_format == "markdown" else f"# Converted\n\n{normalized}"), indent=2, ensure_ascii=True)
        elif target_format == "plain":
            converted = clean_plain(normalized)
        else:
            converted = normalized
    except json.JSONDecodeError:
        return error("source JSON could not be parsed")
    return ok({"content": converted, "source": source_format, "target": target_format})


TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "convert_text_markup",
        "title": "Convert Text Markup",
        "description": "Convert text between plain text, Markdown, HTML, and JSON.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "minLength": 1, "description": "Source text."},
                "source_format": {
                    "type": "string",
                    "enum": ["plain", "markdown", "html", "json"],
                    "description": "Current source format.",
                },
                "target_format": {
                    "type": "string",
                    "enum": ["plain", "markdown", "html", "json"],
                    "description": "Desired target format.",
                },
            },
            "required": ["text", "source_format", "target_format"],
        },
    },
    {
        "name": "markdown_to_html",
        "title": "Markdown → HTML",
        "description": "Convert lightweight Markdown text to HTML.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "minLength": 1, "description": "Markdown text."},
                "preserve_blank_lines": {"type": "boolean", "default": False, "description": "Keep blank lines when possible."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "json_to_markdown",
        "title": "JSON → Markdown",
        "description": "Render JSON objects or arrays as readable Markdown.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "json": {
                    "type": "string",
                    "minLength": 1,
                    "description": "JSON string to render.",
                },
            },
            "required": ["json"],
            "additionalProperties": False,
        },
    },
    {
        "name": "normalize_whitespace",
        "title": "Normalize Whitespace",
        "description": "Trim and collapse runs of whitespace into a clean string.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "minLength": 1, "description": "Source text."},
                "preserve_line_breaks": {"type": "boolean", "default": False, "description": "Keep soft line breaks."},
            },
            "required": ["text"],
        },
    },
    {
        "name": "summarize_text",
        "title": "Summarize Text",
        "description": "Return the first N sentences and basic text metrics.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "minLength": 1, "description": "Source text."},
                "max_sentences": {"type": "integer", "minimum": 1, "maximum": 20, "default": 3},
            },
            "required": ["text"],
        },
    },
    {
        "name": "server_info",
        "title": "Server Info",
        "description": "Return server metadata, rate limit, and supported formats.",
        "inputSchema": {"type": "object"},
    },
]


def dispatch(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if tool_name == "server_info":
        return ok({
            "name": "slatemint",
            "version": "1.0.0",
            "started_at": STARTED_AT,
            "rate_limit": {"free_per_window": RATE_LIMIT, "window_seconds": RATE_WINDOW},
            "pro_key_hint": PRO_KEY,
            "supported_source_formats": ["plain", "markdown", "html", "json"],
            "supported_target_formats": ["plain", "markdown", "html", "json"],
        })

    if not rate_limiter.check():
        return error("Rate limit exceeded. Upgrade to Pro or retry later.", code="rate_limited")

    if tool_name == "convert_text_markup":
        text = arguments.get("text")
        if not isinstance(text, str) or not text.strip():
            return error("text must be a non-empty string")
        source_format = str(arguments.get("source_format", "")).lower()
        target_format = str(arguments.get("target_format", "")).lower()
        return convert_text_markup(text, source_format, target_format)

    if tool_name == "markdown_to_html":
        text = arguments.get("text")
        if not isinstance(text, str) or not text.strip():
            return error("text must be a non-empty string")
        return ok({
            "html": markdown_to_html(text),
            "source_format": "markdown",
            "target_format": "html",
        })

    if tool_name == "json_to_markdown":
        raw = arguments.get("json")
        if not isinstance(raw, str) or not raw.strip():
            return error("json must be a non-empty string")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            return error(f"invalid json: {exc}")
        if isinstance(parsed, dict):
            markdown = structured_to_markdown(parsed)
        else:
            markdown = "\n".join(f"- {item}" for item in parsed)
        return ok({"markdown": markdown, "source_format": "json", "target_format": "markdown"})

    if tool_name == "normalize_whitespace":
        text = arguments.get("text")
        if not isinstance(text, str) or not text.strip():
            return error("text must be a non-empty string")
        preserve_line_breaks = bool(arguments.get("preserve_line_breaks", False))
        cleaned = clean_plain(text, preserve_lines=preserve_line_breaks)
        return ok({"normalized": cleaned, "preserve_line_breaks": preserve_line_breaks})

    if tool_name == "summarize_text":
        text = arguments.get("text")
        if not isinstance(text, str) or not text.strip():
            return error("text must be a non-empty string")
        max_sentences = int(arguments.get("max_sentences", 3) or 3)
        max_sentences = max(1, min(20, max_sentences))
        return ok(summarize(text, max_sentences=max_sentences))

    return error(f"Unknown tool: {tool_name}", code="not_found")


def handle_request(raw: str) -> str:
    payload = json.loads(raw)
    method = payload.get("method")
    request_id = payload.get("id")
    result: dict[str, Any]
    if method == "initialize":
        result = {
            "protocol_version": "2024-11-05",
            "capabilities": {"tools": {}},
            "server_info": {"name": "slatemint", "version": "1.0.0"},
        }
    elif method == "tools/list":
        result = {"tools": TOOL_SPECS}
    elif method == "tools/call":
        tool_name = (payload.get("params") or {}).get("name")
        arguments = (payload.get("params") or {}).get("arguments") or {}
        result = dispatch(tool_name, arguments)
    else:
        result = error("Method not allowed", code="not_supported")
    response = {"jsonrpc": "2.0", "id": request_id}
    if isinstance(result, dict):
        response.update(result)
    else:
        response.update({"ok": False, "error": {"code": "internal", "message": "Invalid handler response"}})
    return encode_json(response)


async def stream_reader() -> None:
    reader: Any = None
    try:
        import asyncio
        loop = asyncio.get_event_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        log("SlateMint MCP server started.")
        buffer = ""
        while True:
            line = await reader.readline()
            if not line:
                break
            buffer += line.decode("utf-8", errors="replace")
            while "\n" in buffer:
                raw, buffer = buffer.split("\n", 1)
                raw = raw.strip()
                if not raw:
                    continue
                sys.stdout.write(handle_request(raw) + "\n")
                sys.stdout.flush()
    except Exception as exc:
        log(f"fatal: {exc}")


def main() -> None:
    try:
        import asyncio
        asyncio.run(stream_reader())
    except Exception as exc:
        log(f"startup failure: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
