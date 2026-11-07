"""Preview a firm's spec as a new memo: memo.json (resolved elements), memo.html (canvas stand-in) and
memo.pptx (export), all built from the spec only (steps 1–4), never from the firm's PowerPoint file.

Usage (from docs/pilots):
    python -m preview.run <firm>
"""
from __future__ import annotations

import argparse
import json
import sys

from preview.elements import MemoBuilder
from preview.painters import to_html, to_pptx


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("firm")
    firm = parser.parse_args().firm
    builder = MemoBuilder(firm)
    memo = builder.build()
    out = builder.out
    (out / "memo.json").write_text(json.dumps(memo, indent=2, ensure_ascii=False), encoding="utf-8")
    (out / "memo.html").write_text(to_html(memo), encoding="utf-8")
    to_pptx(memo, out / "memo.pptx", out / "assets")
    count = sum(len(s["elements"]) for s in memo["slides"])
    sys.stdout.write(f"{out}  slides={len(memo['slides'])} elements={count}\n")


if __name__ == "__main__":
    main()
