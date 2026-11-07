"""The cycle every step shares: one call, code validates the answer, failures go back to the model
with their errors (at most two retries), whatever still fails is left for the person.

A step gives: instructions, the input package (text and optional images), and a validate function
that returns a list of error strings for an answer (empty when the answer is acceptable).
"""
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[3]


def _content(text: str, images: list[Path]) -> list[dict]:
    parts = [{"type": "text", "text": text}]
    for image in images:
        parts.append({"type": "text", "text": f"IMAGE {image.name}"})
        data = base64.b64encode(image.read_bytes()).decode()
        parts.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}})
    return parts


def call_with_checks(instructions: str, text: str, images: list[Path],
                     validate: Callable[[dict], list[str]], retries: int = 2) -> dict:
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv(ROOT / ".env")
    model = os.environ.get("OPENAI_MODEL", "gpt-5.5")
    client = OpenAI()
    messages = [{"role": "system", "content": instructions}, {"role": "user", "content": _content(text, images)}]
    attempts = []
    answer, errors = {}, ["no answer"]
    for attempt in range(retries + 1):
        started = time.time()
        response = client.chat.completions.create(model=model, messages=messages,
                                                  response_format={"type": "json_object"})
        raw = response.choices[0].message.content
        answer = json.loads(raw)
        errors = validate(answer)
        usage = response.usage
        attempts.append({"seconds": round(time.time() - started, 1),
                         "prompt_tokens": usage.prompt_tokens if usage else None,
                         "completion_tokens": usage.completion_tokens if usage else None,
                         "errors": errors})
        if not errors:
            break
        messages += [{"role": "assistant", "content": raw},
                     {"role": "user", "content": "Your answer failed these checks. Fix them and return the full JSON again:\n- "
                                                 + "\n- ".join(errors)}]
    return {"model": model, "answer": answer, "unresolved": errors, "attempts": attempts}
