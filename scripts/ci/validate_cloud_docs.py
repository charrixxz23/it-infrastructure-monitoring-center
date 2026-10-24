#!/usr/bin/env python3
"""Validate local links and basic fenced Mermaid structure in cloud docs."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[2]
MARKDOWN_FILES = [ROOT / "README.md", *sorted((ROOT / "docs" / "cloud").glob("*.md"))]
LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)\s]+)(?:\s+[^)]*)?\)")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})([^\r\n]*)\r?\n([\s\S]*?)^\s*\1\s*$", re.MULTILINE)
MERMAID_START = re.compile(
    r"^(?:flowchart|graph|sequenceDiagram|stateDiagram(?:-v2)?|classDiagram|"
    r"erDiagram|journey|gantt|pie|mindmap|timeline|gitGraph)\b",
    re.IGNORECASE,
)


def heading_anchors(markdown: str) -> set[str]:
    anchors: set[str] = set()
    for line in markdown.splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        title = re.sub(r"[`*_~]", "", match.group(1)).lower()
        title = re.sub(r"[^\w\- ]", "", title, flags=re.UNICODE)
        anchors.add(re.sub(r"\s+", "-", title.strip()))
    return anchors


def validate_links() -> list[str]:
    errors: list[str] = []
    for source in MARKDOWN_FILES:
        content = source.read_text(encoding="utf-8")
        for match in LINK_RE.finditer(content):
            raw = match.group(1).strip("<>")
            parsed = urlsplit(raw)
            if parsed.scheme or raw.startswith("//"):
                continue
            target_path = unquote(parsed.path)
            resolved = (source.parent / target_path).resolve() if target_path else source.resolve()
            if resolved.is_dir() and not parsed.fragment:
                continue
            if resolved.is_dir():
                resolved = resolved / "README.md"
            if not resolved.exists():
                errors.append(f"{source.relative_to(ROOT)}: missing link target {raw}")
                continue
            if parsed.fragment and resolved.suffix.lower() == ".md":
                target_text = resolved.read_text(encoding="utf-8")
                if parsed.fragment not in heading_anchors(target_text):
                    errors.append(f"{source.relative_to(ROOT)}: missing heading #{parsed.fragment} in {raw}")
    return errors


def balanced_mermaid(source: Path, diagram: str) -> str | None:
    stack: list[tuple[str, int]] = []
    closing = {"]": "[", ")": "(", "}": "{"}
    quote: str | None = None
    escaped = False
    for line_number, char in enumerate(diagram, start=1):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ('"', "'"):
            quote = char
        elif char in "[({":
            stack.append((char, line_number))
        elif char in "])}":
            if not stack or stack[-1][0] != closing[char]:
                return f"{source.relative_to(ROOT)}: unmatched {char} in Mermaid block near line {line_number}"
            stack.pop()
    if quote:
        return f"{source.relative_to(ROOT)}: unclosed quote in Mermaid block"
    if stack:
        char, line_number = stack[-1]
        return f"{source.relative_to(ROOT)}: unclosed {char} in Mermaid block near line {line_number}"
    return None


def validate_mermaid() -> list[str]:
    errors: list[str] = []
    block_count = 0
    for source in MARKDOWN_FILES:
        content = source.read_text(encoding="utf-8")
        for fence in FENCE_RE.finditer(content):
            language = fence.group(2).strip().lower()
            if language != "mermaid":
                continue
            block_count += 1
            diagram = fence.group(3).strip()
            if not diagram or not MERMAID_START.match(diagram.splitlines()[0].strip()):
                errors.append(f"{source.relative_to(ROOT)}: invalid or empty Mermaid diagram block")
                continue
            issue = balanced_mermaid(source, diagram)
            if issue:
                errors.append(issue)
    if block_count == 0:
        errors.append("No Mermaid fenced blocks found in checked Markdown files")
    return errors


def main() -> int:
    if not MARKDOWN_FILES[0].is_file():
        print("README.md is missing", file=sys.stderr)
        return 1
    errors = validate_links() + validate_mermaid()
    if errors:
        print("Cloud documentation validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Cloud documentation validation passed: {len(MARKDOWN_FILES)} Markdown files; local links and Mermaid blocks checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
