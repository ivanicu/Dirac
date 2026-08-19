#!/usr/bin/env python3
"""Deterministic OpenAI-compatible failure server for research-loop tests."""
from __future__ import annotations

import argparse
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


VALID_PROPOSAL = {
    "schema_version": "1.0",
    "context_digest": "sha256:" + "a" * 64,
    "summary": "One governed edge can resolve the current ranking ambiguity.",
    "hypothesis_drafts": [],
    "claim_assessments": [],
    "scientific_questions": [],
    "candidate_actions": [],
    "preferred_action_id": None,
    "stop_recommendation": {"recommended": True, "reason_codes": ["TEST_STOP"]},
    "unknowns": [],
    "conflicts": [],
    "warnings": [],
}


class FakeProviderServer(ThreadingHTTPServer):
    def __init__(self, address, handler, *, mode: str):
        super().__init__(address, handler)
        self.mode = mode
        self.attempts = 0


class Handler(BaseHTTPRequestHandler):
    server: FakeProviderServer

    def log_message(self, format, *args):  # noqa: A002
        return

    def _send(self, status: int, body: bytes, **headers: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("x-request-id", f"fake-{self.server.attempts}")
        for name, value in headers.items():
            self.send_header(name.replace("_", "-"), value)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        self.server.attempts += 1
        if self.path != "/v1/chat/completions":
            self._send(404, b'{}')
            return
        length = int(self.headers.get("Content-Length", "0"))
        request = self.rfile.read(length)
        try:
            payload = json.loads(request)
        except json.JSONDecodeError:
            self._send(400, b'{}')
            return
        if payload.get("stream") is not False or payload.get("tools") is not None:
            self._send(400, b'{}')
            return

        mode = self.server.mode
        if mode == "connection-reset":
            self.connection.close()
            return
        if mode == "slow":
            time.sleep(5)
        if mode == "redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1/forbidden")
            self.end_headers()
            return
        if mode == "auth":
            self._send(401, b'{"error":{"message":"not echoed"}}')
            return
        if mode == "forbidden":
            self._send(403, b'{"error":{"message":"not echoed"}}')
            return
        if mode.startswith("retry-") and self.server.attempts < 3:
            status = int(mode.removeprefix("retry-"))
            self._send(status, b'{"error":{}}', Retry_After="0")
            return
        if mode == "invalid-json":
            self._send(200, b'not json')
            return
        if mode == "oversized":
            self._send(200, b'{' + b' ' * 300000 + b'}')
            return

        content: object = VALID_PROPOSAL
        message: dict[str, object] = {"role": "assistant"}
        finish_reason = "stop"
        if mode == "markdown":
            message["content"] = "```json\n{}\n```"
        elif mode == "schema-invalid" or (
            mode == "invalid-then-valid" and self.server.attempts == 1
        ):
            message["content"] = json.dumps({"schema_version": "wrong"})
        else:
            message["content"] = json.dumps(content, separators=(",", ":"))
        if mode == "reasoning":
            message["reasoning_content"] = "must never leave the transport"
        if mode == "tool-call":
            message["tool_calls"] = [{"id": "forbidden", "type": "function"}]
        if mode == "length":
            finish_reason = "length"
        response = {
            "id": f"fake-{self.server.attempts}",
            "model": "fake-qwen-resolved",
            "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        }
        self._send(200, json.dumps(response).encode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument(
        "--mode",
        default="valid",
        choices=(
            "valid", "markdown", "invalid-json", "schema-invalid", "invalid-then-valid", "reasoning",
            "tool-call", "length", "oversized", "slow", "connection-reset",
            "redirect", "auth", "forbidden", "retry-429", "retry-500", "retry-502",
            "retry-503", "retry-504",
        ),
    )
    args = parser.parse_args()
    server = FakeProviderServer((args.host, args.port), Handler, mode=args.mode)
    print(json.dumps({"host": args.host, "port": server.server_port, "mode": args.mode}), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
