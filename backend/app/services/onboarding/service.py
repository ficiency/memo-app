"""The onboarding as the app runs it: a firm's materials, its six steps (each run, reviewed and approved by a
person), and the spec version written from the approved steps. Nothing here knows any firm.

Step results stay files under backend/.work/onboarding/<firm>/ (as the steps write them); approvals are stored
in onboarding_runs and onboarding_steps. A step runs in a background thread: steps 1 to 5 call the model.
"""
from __future__ import annotations

import json
import shutil
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import REPO
from app.db import spec as S
from app.db.session import SessionLocal
from app.services.onboarding.common import firm_dir

STEPS = {0: "Materials", 1: "House style", 2: "Structure", 3: "Layouts", 4: "Content recipe", 5: "Rules"}
KINDS = ("guide", "template", "memo", "logo")
PRICE_IN, PRICE_OUT = 5 / 1e6, 30 / 1e6          # USD per token, the onboarding model
RUNNING: dict[tuple[str, int], dict] = {}        # (firm, step) → {"started", "error"}
LOCK = threading.Lock()


# --- materials -----------------------------------------------------------------------------------------
def manifest_path(firm: str) -> Path:
    """The firm's working list of materials: starts from the repo's manifest, grows with uploads."""
    path = firm_dir(firm) / "manifest.json"
    if not path.exists():
        source = REPO / "backend" / "onboarding_firms" / f"{firm}.json"
        if source.exists():
            shutil.copy(source, path)
        else:
            path.write_text(json.dumps({"firm": firm, "materials": []}, indent=1), encoding="utf-8")
    return path


def materials(firm: str) -> list[dict]:
    manifest = json.loads(manifest_path(firm).read_text(encoding="utf-8"))
    out = []
    for m in manifest["materials"]:
        file = REPO / m["file"]
        out.append({"kind": m["kind"], "name": file.name, "size": file.stat().st_size if file.exists() else None})
    return out


def add_material(firm: str, kind: str, name: str, content: bytes) -> dict:
    if kind not in KINDS:
        raise ValueError(f"a material is one of {', '.join(KINDS)}")
    folder = firm_dir(firm) / "materials"
    folder.mkdir(parents=True, exist_ok=True)
    file = folder / Path(name).name
    file.write_bytes(content)
    path = manifest_path(firm)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    rel = file.relative_to(REPO).as_posix()
    manifest["materials"] = [m for m in manifest["materials"] if m["file"] != rel] + [{"kind": kind, "file": rel}]
    path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return {"kind": kind, "name": file.name, "size": len(content)}


# --- steps ---------------------------------------------------------------------------------------------
def result_file(firm: str, n: int) -> Path:
    return firm_dir(firm) / f"step_{n}.json"


def load(firm: str, n: int) -> dict | None:
    f = result_file(firm, n)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def run_record(session: Session, firm: S.Firm) -> S.OnboardingRun:
    run = session.scalar(select(S.OnboardingRun).where(S.OnboardingRun.firm_id == firm.id).order_by(S.OnboardingRun.created_at.desc()))
    if run is None:
        run = S.OnboardingRun(firm_id=firm.id, status="review")
        session.add(run)
        session.flush()
    rows = {s.number: s for s in session.scalars(select(S.OnboardingStep).where(S.OnboardingStep.run_id == run.id))}
    for n in STEPS:
        if n not in rows:
            session.add(S.OnboardingStep(run_id=run.id, number=n, status="pending"))
    session.commit()
    return run


def steps(session: Session, firm: S.Firm) -> list[dict]:
    run = run_record(session, firm)
    rows = {s.number: s for s in session.scalars(select(S.OnboardingStep).where(S.OnboardingStep.run_id == run.id))}
    out = []
    for n, name in STEPS.items():
        data = load(firm.name, n)
        row = rows[n]
        attempts = (data or {}).get("attempts") or []
        tokens_in = sum(a.get("prompt_tokens") or 0 for a in attempts)
        tokens_out = sum(a.get("completion_tokens") or 0 for a in attempts)
        answer = (data or {}).get("answer") if isinstance((data or {}).get("answer"), dict) else (data or {})
        running = RUNNING.get((firm.name, n))
        approver = session.get(S.User, row.approved_by) if row.approved_by else None
        out.append({
            "number": n, "name": name,
            "status": "running" if running and not running.get("error") else
                      "failed" if running and running.get("error") else
                      "approved" if row.approved_at and data else "review" if data else "pending",
            "error": running.get("error") if running else None,
            "ran_at": datetime.fromtimestamp(result_file(firm.name, n).stat().st_mtime, timezone.utc).isoformat() if data else None,
            "approved_at": row.approved_at.isoformat() if row.approved_at and data else None,
            "approved_by": approver.email if approver else None,
            "model_calls": len(attempts), "cost_usd": round(tokens_in * PRICE_IN + tokens_out * PRICE_OUT, 2),
            "unresolved": len((data or {}).get("unresolved") or []),
            "conflicts": len((answer or {}).get("conflicts") or []),
            "questions": len((answer or {}).get("questions") or (data or {}).get("open_questions") or []),
        })
    return out


def approve(session: Session, firm: S.Firm, n: int, user: S.User) -> None:
    if load(firm.name, n) is None:
        raise ValueError("this step has not run yet")
    run = run_record(session, firm)
    row = session.scalar(select(S.OnboardingStep).where(S.OnboardingStep.run_id == run.id, S.OnboardingStep.number == n))
    row.status, row.approved_by, row.approved_at = "approved", user.id, datetime.now(timezone.utc)
    session.commit()


def start(firm: str, n: int) -> None:
    """Run a step in the background; its approval and the later steps' approvals are cleared (they were made on
    the step's previous result)."""
    with LOCK:
        if any(k[0] == firm and not v.get("error") for k, v in RUNNING.items()):
            raise ValueError("a step of this firm is already running")
        if n > 0 and load(firm, n - 1) is None:
            raise ValueError(f"step {n - 1} has not run yet")
        RUNNING[(firm, n)] = {"started": time.time(), "error": None}

    def work():
        try:
            from app.services.onboarding import (step0_extract, step1_house_style, step2_structure, step3_layouts,
                                                 step4_recipe, step5_rules)
            if n == 0:
                step0_extract.run(manifest_path(firm))
            else:
                {1: step1_house_style, 2: step2_structure, 3: step3_layouts, 4: step4_recipe, 5: step5_rules}[n].run(firm)
            with SessionLocal() as session:
                f = session.scalar(select(S.Firm).where(S.Firm.name == firm))
                run = run_record(session, f)
                for row in session.scalars(select(S.OnboardingStep).where(S.OnboardingStep.run_id == run.id, S.OnboardingStep.number >= n)):
                    row.status, row.approved_by, row.approved_at = "review", None, None
                session.commit()
            RUNNING.pop((firm, n), None)
        except BaseException as e:                      # a step may stop with SystemExit on bad materials
            RUNNING[(firm, n)] = {"started": time.time(), "error": f"{type(e).__name__}: {e}"}
            traceback.print_exc()

    threading.Thread(target=work, daemon=True).start()
