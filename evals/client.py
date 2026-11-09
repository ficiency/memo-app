"""How the evals talk to the system: the way the app does, never around it.

A server of its own (uvicorn on a free port), HTTP with the person's email (the API the screens call), and a
websocket per person in a memo's room (what the canvas sends: operations, takes, drafts). The database is read
only to observe what happened (the facts an expectation is checked against), never to make a change.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

import websockets  # noqa: E402


class Server:
    def __init__(self, port: int):
        self.port = port
        self.base = f"http://localhost:{port}"
        self.proc = None

    def __enter__(self):
        self.proc = subprocess.Popen([str(REPO / "backend" / ".venv" / "Scripts" / "uvicorn"), "app.main:app", "--port", str(self.port)],
                                     cwd=REPO / "backend", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(80):
            try:
                urllib.request.urlopen(f"{self.base}/health", timeout=2)
                return self
            except Exception:
                time.sleep(0.5)
        self.proc.kill()
        raise RuntimeError("the server did not start")

    def __exit__(self, *exc):
        if self.proc:
            self.proc.kill()
            self.proc.wait(timeout=10)


class Person:
    """One person: the API with their email, and their websocket in a memo."""

    def __init__(self, server: Server, email: str):
        self.server, self.email = server, email
        self.ws = None
        self.inbox: list[dict] = []

    # --- HTTP ---------------------------------------------------------------------------------------------
    def call(self, method: str, path: str, body=None, raw: bytes | None = None, ctype="application/json") -> tuple[int, object]:
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.server.base + path, method=method, data=data,
                                     headers={"X-User-Email": self.email, "Content-Type": ctype})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                payload = r.read()
                kind = r.headers.get("content-type", "")
                return r.status, (json.loads(payload) if "json" in kind else payload)
        except urllib.error.HTTPError as e:
            payload = e.read()
            try:
                return e.code, json.loads(payload)
            except ValueError:
                return e.code, payload

    def get(self, path):
        return self.call("GET", path)

    async def aget(self, path):
        """GET off the event loop: a slow answer (an export) must not stop the websocket answering the server's pings."""
        return await asyncio.to_thread(self.get, path)

    def post(self, path, body=None):
        return self.call("POST", path, body)

    def upload(self, path: str, name: str, data: bytes, ctype: str):
        boundary = uuid.uuid4().hex
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
                f"Content-Type: {ctype}\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
        return self.call("POST", path, raw=body, ctype=f"multipart/form-data; boundary={boundary}")

    # --- the memo's room ------------------------------------------------------------------------------------
    async def join(self, memo_id: str):
        url = f"ws://localhost:{self.server.port}/memos/{memo_id}/live?email={self.email}"
        self.ws = await websockets.connect(url, max_size=None, ping_interval=20, ping_timeout=120)
        return self

    async def leave(self):
        if self.ws:
            await self.ws.close()

    async def op(self, op: dict, timeout=120) -> dict:
        """Send an operation as the canvas does and wait for the door's answer to it (applied, refused, kept aside)."""
        await self.ws.send(json.dumps({"type": "op", "op": op}))
        target = str(op.get("target") or "")

        async def wait():
            while True:
                m = json.loads(await self.ws.recv())
                self.inbox.append(m)
                if m.get("type") == "operation" and m.get("by") == self.email and m.get("target") == target:
                    return m
        return await asyncio.wait_for(wait(), timeout)

    async def send(self, message: dict):
        await self.ws.send(json.dumps(message))


def latest_spec_and_record(person: Person) -> tuple[str, str, list[str]]:
    """(spec version id, record revision id, stages) the new-memo screen would offer first."""
    _, specs = person.get("/specs")
    version = specs[0]["versions"][0]
    _, deals = person.get("/records")
    return version["id"], deals[0]["revisions"][0]["id"], version["stages"]
