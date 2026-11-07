"""Write numbers and dates the way a firm writes them, from its formats as fields.

One formatter for the whole system: the onboarding uses it to check that the formats it extracted
reproduce the firm's own examples, and the generator uses it to write every figure. Nothing here knows
any firm; a format is a plain dict with the fields of `formats` (+ its scale rules).

Values arrive normalised (deal record conventions): money in whole currency units, percentages as
fractions (0.25 = 25%), multiples as plain numbers (4.5 = 4.5x), dates as ISO strings.
"""
from __future__ import annotations

import re
from datetime import date

SCALES = {"units": 1, "k": 1_000, "m": 1_000_000, "bn": 1_000_000_000}
NUMERIC = ("money", "percent", "multiple", "count", "per_share", "currency")
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]


def _digits(x: float, decimals: int, thousands: str, decimal: str) -> str:
    text = f"{abs(x):,.{decimals}f}"
    return text.replace(",", "\0").replace(".", decimal).replace("\0", thousands)


def _negative(text: str, style: str | None) -> str:
    if style == "parentheses":
        return f"({text})"
    if style == "unicode_minus":
        return f"−{text}"
    return f"-{text}"


def format_number(value: float, fmt: dict) -> str:
    """value in record units → the firm's text. fmt keys: item, decimals, symbol, symbol_position
    (before|after), unit_display (inline|header), negative_style (minus|parentheses|unicode_minus),
    thousands_separator, decimal_separator, suffix, scales [{scale, from_value, decimals}]."""
    item = fmt.get("item")
    thousands = fmt.get("thousands_separator") if fmt.get("thousands_separator") is not None else ","
    decimal = fmt.get("decimal_separator") or "."
    decimals = fmt.get("decimals") if isinstance(fmt.get("decimals"), int) else 0
    suffix = fmt.get("suffix") or ""
    scaled, scale_word = value, ""
    if item == "percent":
        scaled, suffix = value * 100, suffix or "%"
    elif item in ("money", "currency", "per_share", "count"):
        rules = sorted(fmt.get("scales") or [], key=lambda r: r.get("from_value") or 0)
        chosen = None
        for rule in rules:
            if abs(value) >= (rule.get("from_value") or 0):
                chosen = rule
        if chosen:
            scaled = value / SCALES.get(chosen.get("scale") or "units", 1)
            decimals = chosen["decimals"] if isinstance(chosen.get("decimals"), int) else decimals
            if fmt.get("unit_display", "inline") == "inline" and chosen.get("scale") not in (None, "units"):
                scale_word = chosen["scale"]
    body = _digits(scaled, decimals, thousands, decimal)
    if float(body.replace(thousands, "").replace(decimal, ".") or 0) == 0:
        negative = False                                       # "-0.0" reads as zero
    else:
        negative = scaled < 0
    symbol = fmt.get("symbol") or ""
    if fmt.get("unit_display", "inline") == "header":
        symbol = ""
    if symbol and fmt.get("symbol_position", "before") == "after":
        text = f"{body}{scale_word}{suffix} {symbol}".strip()
    else:
        text = f"{symbol}{body}{scale_word}{suffix}"
    return _negative(text, fmt.get("negative_style")) if negative else text


def format_date(iso: str, fmt: dict) -> str:
    """pattern tokens: YYYY YY MMMM MMM MM M DD D (anything else is kept)."""
    d = date.fromisoformat(iso[:10])
    tokens = {"YYYY": f"{d.year}", "YY": f"{d.year % 100:02d}", "MMMM": MONTHS[d.month - 1],
              "MMM": MONTHS[d.month - 1][:3], "MM": f"{d.month:02d}", "M": f"{d.month}",
              "DD": f"{d.day:02d}", "D": f"{d.day}"}
    # date tokens are not ambiguous, so the pattern may come in either case ("d mmmm yyyy")
    return re.sub("YYYY|YY|MMMM|MMM|MM|M|DD|D", lambda m: tokens[m.group(0)], (fmt.get("pattern") or "YYYY-MM-DD").upper())


def date_matches(example: str, fmt: dict) -> bool:
    """Whether an example date fits the pattern (used to check an extracted pattern)."""
    source = (fmt.get("pattern") or "").upper()
    rx = {"YYYY": r"\d{4}", "YY": r"\d{2}", "MMMM": "|".join(MONTHS), "MMM": "|".join(m[:3] for m in MONTHS),
          "MM": r"\d{2}", "M": r"\d{1,2}", "DD": r"\d{2}", "D": r"\d{1,2}"}
    # one pass over the pattern, so a month name put in for MMMM is never read as a token again
    parts, pos = [], 0
    for m in re.finditer("YYYY|YY|MMMM|MMM|MM|M|DD|D", source):
        parts.append(re.escape(source[pos:m.start()]) + f"(?:{rx[m.group(0)]})")
        pos = m.end()
    pattern = "".join(parts) + re.escape(source[pos:])
    return bool(source) and re.fullmatch(pattern, example.strip(), re.I) is not None


def parse_example(example: str, fmt: dict) -> float | None:
    """The value a numeric example stands for, in record units ("£22.9m" → 22_900_000), read with the
    format's own separators; None when it is not a plain number of this kind."""
    text = example.strip().replace("−", "-")
    negative = text.startswith("(") and text.endswith(")") or text.startswith("-")
    text = text.strip("()-").strip()
    symbol = fmt.get("symbol") or ""
    if symbol:
        text = text.replace(symbol, "").strip()
    if fmt.get("item") == "percent":
        text = text.rstrip("%").strip()
    suffix = fmt.get("suffix") or ""
    if suffix and text.endswith(suffix):
        text = text[: -len(suffix)].strip()
    scale = 1
    m = re.fullmatch(r"(.*?)(bn|k|m)", text)
    if m and fmt.get("item") in ("money", "currency", "per_share", "count"):
        text, scale = m.group(1).strip(), SCALES[m.group(2)]
    elif fmt.get("unit_display") == "header" and fmt.get("scales"):
        # the unit is written in the column header ("£k"), so a bare number is in that scale
        first = sorted(fmt["scales"], key=lambda r: r.get("from_value") or 0)[0]
        scale = SCALES.get(first.get("scale") or "units", 1)
    thousands = fmt.get("thousands_separator") if fmt.get("thousands_separator") is not None else ","
    decimal = fmt.get("decimal_separator") or "."
    if thousands:
        text = text.replace(thousands, "")
    text = text.replace(decimal, ".")
    if not re.fullmatch(r"\d+(\.\d+)?", text):
        return None
    value = float(text) * scale
    if fmt.get("item") == "percent":
        value /= 100
    return -value if negative else value


def reproduces(example: str, fmt: dict) -> tuple[bool, str]:
    """Check one firm example against a format: parse it, write it back, compare. (ok, what came out)."""
    item = fmt.get("item")
    if item == "date":
        return date_matches(example, fmt), fmt.get("pattern") or ""
    if item not in NUMERIC:
        return True, example                                    # periods and others: not checked here
    value = parse_example(example, fmt)
    if value is None:
        return False, "(not a number this format can read)"
    out = format_number(value, fmt)
    return out == example.strip(), out
