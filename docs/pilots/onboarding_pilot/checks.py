"""Checks shared by the steps: evidence quoted from the guide must exist in it."""
from __future__ import annotations

import re


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9%€£$.]+", " ", (text or "").lower()).strip()


def quote_found(quote: str, guide_norm: str) -> bool:
    """Tolerant: any 5 consecutive words of the quote (or the whole quote if shorter) are in the guide."""
    words = norm(quote).split()
    if len(words) < 3:
        return True
    window = min(5, len(words))
    return any(" ".join(words[i:i + window]) in guide_norm for i in range(len(words) - window + 1))


def evidences(answer: dict):
    """Every (where, evidence) pair in an answer."""
    def visit(node, path):
        if isinstance(node, dict):
            if isinstance(node.get("evidence"), dict):
                yield path, node["evidence"]
            for key, value in node.items():
                if key != "evidence":
                    yield from visit(value, f"{path}.{key}" if path else key)
        elif isinstance(node, list):
            for i, value in enumerate(node):
                yield from visit(value, f"{path}[{i}]")
    yield from visit(answer, "")


def guide_quote_errors(answer: dict, guide: str | None) -> list[str]:
    errors = []
    guide_norm = norm(guide) if guide else ""
    for where, ev in evidences(answer):
        if ev.get("source") == "guide":
            if not guide:
                errors.append(f"{where}: cites the guide, but there is no guide")
            elif not quote_found(ev.get("quote", ""), guide_norm):
                errors.append(f"{where}: quote not found in the guide: {str(ev.get('quote'))[:80]!r}")
    return errors
