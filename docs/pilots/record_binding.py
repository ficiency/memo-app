"""Pilot: connect the concepts found in a firm's memos (memo_patterns.py) to paths in a deal record.

The recipe of a firm says "this box holds cheque_size". Which datum of the deal record that is
depends on the record, not on the firm, so it is a separate step: one model call with the list of
concepts and a compact index of the record. Code then checks every path the model returns.

Paths:
    field:<id>                                  one value
    table:<table>.<column>                      a column (one value per row)
    table:<table>[<column>=<value>].<column>    one cell, selected by a row
    angle_table:<angle>.<table>.<column>        same, for a table inside an angle
    angle_table:<angle>.<table>[<column>=<value>].<column>
    series:<name>                               a monthly series
    narrative:<key>                             text (thesis, risks, team...)
    company:<key>                               company identity (name, entities...)
    angle:<angle>.<key>                         an angle's own text or list

Usage (from docs/pilots):
    python record_binding.py index <record.json>
    python record_binding.py run <firm> <record.json>
    python record_binding.py check <firm> <record.json>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "out"

# Which record kinds can carry which concept kinds.
COMPATIBLE = {
    "money": {"money"}, "percent": {"percent"}, "multiple": {"multiple", "number"},
    "count": {"count", "number"}, "date": {"date", "text", "count", "number"},
    "period": {"text", "date", "months", "number"},
    "ratio": {"multiple", "number", "percent"}, "other": None,
}

INSTRUCTIONS = """You connect the concepts a firm uses in its investment memos to the data in a deal record.

You get a list of concepts. Each has a kind (money, percent, multiple, count, date, period, fact...),
where the firm uses it and an example from a past memo of another deal. You also get an index of
the deal record: every field with its id, label, kind, unit and period; every table with its columns
and the first column's values; series; narrative keys; angles (each firm's view of the deal).

For every concept, return the record path that holds it for this deal, or say it is missing.
Path forms:
  field:<id> | table:<table>.<column> | table:<table>[<column>=<value>].<column>
  angle_table:<angle>.<table>.<column> | angle_table:<angle>.<table>[<column>=<value>].<column>
  series:<name> | narrative:<key> | company:<key> | angle:<angle>.<key>

Rules:
- Use only paths that exist in the index. Never invent an id.
- Prefer the angle that matches this firm's position in the deal when a concept has one.
- A concept shown per period in a table (a row of a financial table) maps to a table column.
- If the record holds the ingredients but not the value itself, give "derive": the formula over paths.
- If nothing fits, status "missing" and say what would be needed.

Return JSON only:
{"bindings": [{"concept": "...", "status": "found|derive|missing", "path": "...",
               "derive": "...", "confidence": "high|low", "why": "..."}]}
"""


# ------------------------------------------------------------------ record index

def record_index(record: dict) -> str:
    out = [f"RECORD {record.get('record_id')} as of {record.get('as_of')} currency {record.get('reporting_currency')}"]
    out.append("COMPANY keys: " + ", ".join(record.get("company", {}).keys()))
    out.append("FIELDS (id | label | kind | unit | period)")
    for f in record.get("fields", []):
        out.append(f"  {f['id']} | {f['label']} | {f['kind']} | {f.get('unit')} | {f.get('period')}")

    def table_lines(prefix: str, name: str, table: dict) -> list[str]:
        cols = ", ".join(f"{c['id']}({c['kind']})" for c in table["columns"])
        first = table["columns"][0]["id"]
        keys = [str(r[0]) for r in table["rows"]]
        return [f"  {prefix}{name}: columns {cols}; {first} values: {keys}; unit {table.get('unit')}"]

    out.append("TABLES")
    for name, table in record.get("tables", {}).items():
        out += table_lines("table:", name, table)
    out.append("SERIES: " + ", ".join(f"{k} ({v.get('unit')}, {v['x'][0]}..{v['x'][-1]})" for k, v in record.get("series", {}).items()))
    out.append("NARRATIVE keys: " + ", ".join(record.get("narrative", {}).keys()))
    for angle, body in record.get("angles", {}).items():
        out.append(f"ANGLE {angle} (firm: {body.get('firm')}): keys {[k for k in body if k != 'tables']}")
        for name, table in body.get("tables", {}).items():
            out += table_lines(f"angle_table:{angle}.", name, table)
    return "\n".join(out)


# ------------------------------------------------------------------ concepts from the patterns pilot

def concepts_of(firm: str) -> list[dict]:
    answer = json.loads((OUT / f"{firm}.patterns.json").read_text(encoding="utf-8"))["answer"]
    found: dict[str, dict] = {}

    def add(concept, kind, where, example):
        if not concept:
            return
        entry = found.setdefault(concept, {"concept": concept, "kind": kind, "used_in": [], "example": example})
        if where not in entry["used_in"]:
            entry["used_in"].append(where)

    for section in answer.get("sections", []):
        for box in section.get("boxes", []):
            where = f"{section['section']} / {box['box']}"
            for seg in box.get("pattern", []):
                example = next(iter((seg.get("examples") or {}).values()), None)
                if seg.get("type") == "figure":
                    add(seg.get("concept"), seg.get("kind"), where, example)
                elif seg.get("type") == "fact":
                    add(seg.get("concept"), "fact", where, example)
                elif seg.get("type") == "table":
                    for row in seg.get("rows", []):
                        if row.get("cells") in ("figure", "fact", "mixed") and row.get("concept"):
                            add(row["concept"], row.get("kind") or "fact", f"{where} / row {row.get('label')}",
                                next(iter((row.get("example") or {}).values()), None))
    return list(found.values())


# ------------------------------------------------------------------ model call

def run(firm: str, record_path: Path) -> None:
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv(ROOT / ".env")
    model = os.environ.get("OPENAI_MODEL", "gpt-5.5")
    record = json.loads(record_path.read_text(encoding="utf-8"))
    concepts = concepts_of(firm)
    prompt = (f"FIRM {firm}\n\nCONCEPTS\n{json.dumps(concepts, ensure_ascii=False, indent=1)}\n\n"
              f"DEAL RECORD INDEX\n{record_index(record)}")
    started = time.time()
    response = OpenAI().chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": INSTRUCTIONS}, {"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    result = {"model": model, "seconds": round(time.time() - started, 1),
              "usage": response.usage.model_dump() if response.usage else {},
              "concepts": concepts, "answer": json.loads(response.choices[0].message.content)}
    target = OUT / f"{firm}.bindings.json"
    target.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    sys.stdout.write(f"{target}  concepts={len(concepts)} seconds={result['seconds']} "
                     f"prompt_tokens={result['usage'].get('prompt_tokens')} completion_tokens={result['usage'].get('completion_tokens')}\n")


# ------------------------------------------------------------------ checks

SELECTOR = re.compile(r"^(?P<table>[^\[.]+)(?:\[(?P<col>[^=\]]+)=(?P<val>[^\]]+)\])?\.(?P<column>.+)$")


def resolve(record: dict, path: str) -> tuple[bool, str | None, str]:
    """(exists, kind of the datum, reason)."""
    prefix, _, rest = path.partition(":")
    fields = {f["id"]: f for f in record.get("fields", [])}
    if prefix == "field":
        f = fields.get(rest)
        return (True, f["kind"], "") if f else (False, None, "no such field")
    if prefix in ("table", "angle_table"):
        if prefix == "angle_table":
            angle, _, rest = rest.partition(".")
            tables = record.get("angles", {}).get(angle, {}).get("tables", {})
        else:
            tables = record.get("tables", {})
        m = SELECTOR.match(rest)
        if not m or m["table"] not in tables:
            return False, None, "no such table"
        table = tables[m["table"]]
        cols = {c["id"]: (i, c) for i, c in enumerate(table["columns"])}
        if m["column"] not in cols:
            return False, None, f"no column {m['column']!r}"
        if m["col"]:
            if m["col"] not in cols:
                return False, None, f"no column {m['col']!r} to select a row"
            i = cols[m["col"]][0]
            if not any(str(r[i]) == m["val"] for r in table["rows"]):
                return False, None, f"no row with {m['col']}={m['val']}"
        return True, cols[m["column"]][1]["kind"], ""
    if prefix == "series":
        return (rest in record.get("series", {}), None, "" if rest in record.get("series", {}) else "no such series")
    if prefix == "narrative":
        key = rest.split(".")[0].split("[")[0]
        return (key in record.get("narrative", {}), "text", "" if key in record.get("narrative", {}) else "no such narrative key")
    if prefix == "company":
        key = rest.split(".")[0].split("[")[0]
        return (key in record.get("company", {}), "text", "" if key in record.get("company", {}) else "no such company key")
    if prefix == "angle":
        angle, _, key = rest.partition(".")
        body = record.get("angles", {}).get(angle)
        key = key.split(".")[0].split("[")[0]
        return (bool(body) and key in body, "text", "" if body and key in body else "no such angle key")
    return False, None, f"unknown path form {prefix!r}"


def check(firm: str, record_path: Path) -> None:
    record = json.loads(record_path.read_text(encoding="utf-8"))
    result = json.loads((OUT / f"{firm}.bindings.json").read_text(encoding="utf-8"))
    kinds = {c["concept"]: c["kind"] for c in result["concepts"]}
    bindings = result["answer"].get("bindings", [])
    counts = {"found": 0, "derive": 0, "missing": 0}
    problems, missing = [], []
    for b in bindings:
        counts[b.get("status", "missing")] = counts.get(b.get("status", "missing"), 0) + 1
        if b.get("status") == "found":
            ok, kind, reason = resolve(record, b.get("path", ""))
            if not ok:
                problems.append(f"PATH DOES NOT EXIST  {b['concept']} -> {b.get('path')} ({reason})")
                continue
            allowed = COMPATIBLE.get(kinds.get(b["concept"]))
            if allowed and kind and kind not in allowed:
                problems.append(f"KIND MISMATCH  {b['concept']} ({kinds.get(b['concept'])}) -> {b['path']} ({kind})")
        elif b.get("status") == "derive":
            # A path ends at whitespace or punctuation, except inside [...] where a row value may hold spaces.
            for path in re.findall(r"(?:field|table|angle_table|series|narrative|company|angle):(?:[^\s,;()*/+\-\[]|\[[^\]]*\])+",
                                   b.get("derive", "")):
                path = path.rstrip(".")
                ok, _, reason = resolve(record, path)
                if not ok:
                    problems.append(f"DERIVE USES MISSING PATH  {b['concept']}: {path} ({reason})")
        else:
            missing.append(f"{b['concept']}: {b.get('why', '')[:140]}")
    unanswered = sorted(set(kinds) - {b["concept"] for b in bindings})
    print(f"concepts={len(kinds)} answered={len(bindings)} found={counts['found']} derive={counts['derive']} "
          f"missing={counts['missing']} problems={len(problems)} unanswered={len(unanswered)}")
    for p in problems:
        print("  " + p)
    for m in missing:
        print("  MISSING  " + m)
    if unanswered:
        print("  NOT ANSWERED  " + ", ".join(unanswered))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    i = sub.add_parser("index")
    i.add_argument("record", type=Path)
    for name in ("run", "check"):
        p = sub.add_parser(name)
        p.add_argument("firm")
        p.add_argument("record", type=Path)
    args = parser.parse_args()
    if args.command == "index":
        text = record_index(json.loads(args.record.read_text(encoding="utf-8")))
        (OUT / "record.index.txt").write_text(text, encoding="utf-8")
        print(f"{OUT / 'record.index.txt'}  chars={len(text)} ~tokens={len(text) // 4}")
    elif args.command == "run":
        run(args.firm, args.record)
    else:
        check(args.firm, args.record)


if __name__ == "__main__":
    main()
