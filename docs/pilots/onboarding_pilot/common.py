"""Shared helpers for the onboarding pilot: where each firm's files live, and reading and writing
step results. Nothing here knows about any firm."""
from __future__ import annotations

import json
from pathlib import Path

PILOTS = Path(__file__).resolve().parents[1]
OUT = PILOTS / "out" / "onboarding"


def firm_dir(firm: str) -> Path:
    path = OUT / firm
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_manifest(path: Path) -> dict:
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    base = Path(path).resolve().parent
    for material in manifest["materials"]:
        material["file"] = str((base / material["file"]).resolve())
    return manifest


def save_result(firm: str, step: int, result: dict) -> None:
    """Save a step's result as soon as it exists, so nothing after it can lose a model call."""
    (firm_dir(firm) / f"step_{step}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")


def save_step(firm: str, step: int, result: dict, review: str) -> Path:
    folder = firm_dir(firm)
    save_result(firm, step, result)
    (folder / f"step_{step}.review.txt").write_text(review, encoding="utf-8")
    return folder / f"step_{step}.review.txt"


def load_step(firm: str, step: int) -> dict:
    return json.loads((firm_dir(firm) / f"step_{step}.json").read_text(encoding="utf-8"))
