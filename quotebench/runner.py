"""OpenAI-compatible chat completions client for the public `run` command.

The client needs a base URL, an API key, and a model identifier. It sends one
request per generation with urllib and returns the reply text, token usage,
and latency. Nothing in this module is specific to one provider.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

DEFAULT_BASE_URL = "https://api.openai.com/v1"
BASE_URL_ENV = "QUOTEBENCH_API_BASE"
API_KEY_ENV = "QUOTEBENCH_API_KEY"
MODEL_ENV = "QUOTEBENCH_MODEL"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n(.*?)\n?```\s*$", re.DOTALL)


def clean_reply_with_meta(text: str) -> tuple[str, list]:
    """Strip model wrappers from a reply while recording every transformation.

    The executed command is the cleaned text. The verbatim text is kept in the
    record so the cleanup is auditable.
    """
    cleanup = []
    t = text
    stripped = t.strip()
    if stripped != t:
        cleanup.append("outer-whitespace")
    t = stripped
    # reasoning models may wrap CoT in <think>...</think> before the answer;
    # keep only what follows the last close tag
    if "</think>" in t:
        cleanup.append("think-block")
        t = t.rsplit("</think>", 1)[1].strip()
    m = _FENCE_RE.match(t)
    if m:
        cleanup.append("markdown-fence")
        t = m.group(1).strip()
    if t.startswith("$ "):
        cleanup.append("shell-prompt")
        t = t[2:]
    return t, cleanup


@dataclass(frozen=True)
class Generation:
    raw_text: str
    text: str
    cleanup: list
    usage: dict
    latency: float
    finish_reason: str | None


def usage_summary(usage: Any) -> dict:
    """Reduce a provider usage object to prompt/completion/reasoning/total counts."""
    if not isinstance(usage, Mapping):
        return {}
    out = {}
    for key, source in (("prompt", "prompt_tokens"),
                        ("completion", "completion_tokens"),
                        ("total", "total_tokens")):
        if isinstance(usage.get(source), int):
            out[key] = usage[source]
    details = usage.get("completion_tokens_details")
    if isinstance(details, Mapping) and isinstance(details.get("reasoning_tokens"), int):
        out["reasoning"] = details["reasoning_tokens"]
    return out


def _first_choice(payload: Any) -> Mapping[str, Any]:
    choices = payload.get("choices") if isinstance(payload, Mapping) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise ValueError(f"response has no choices: {json.dumps(payload)[:500]}")
    return choices[0]


class ChatClient:
    def __init__(self, base_url: str, api_key: str, model: str, *,
                 max_tokens: int | None = None, timeout: float = 120.0,
                 extra_body: Mapping[str, Any] | None = None,
                 max_retries: int = 5, backoff_s: float = 2.0) -> None:
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key
        self.model = model
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.extra_body = dict(extra_body or {})
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        overlap = {"model", "messages"} & set(self.extra_body)
        if overlap:
            raise ValueError(f"extra body overrides required fields: {sorted(overlap)}")

    def complete(self, system_prompt: str, user_message: str) -> Generation:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        }
        if self.max_tokens is not None:
            body["max_tokens"] = self.max_tokens
        body.update(self.extra_body)
        payload, latency = self._post(json.dumps(body).encode("utf-8"))
        choice = _first_choice(payload)
        message = choice.get("message")
        content = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(content, str):
            raise ValueError(
                f"response message has no text content: {json.dumps(choice)[:500]}"
            )
        text, cleanup = clean_reply_with_meta(content)
        finish_reason = choice.get("finish_reason")
        return Generation(
            raw_text=content, text=text, cleanup=cleanup,
            usage=usage_summary(payload.get("usage")), latency=round(latency, 3),
            finish_reason=str(finish_reason) if finish_reason is not None else None,
        )

    def _post(self, data: bytes) -> tuple[Mapping[str, Any], float]:
        """POST once, retrying HTTP 429/5xx with exponential backoff."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        for attempt in range(self.max_retries + 1):
            request = urllib.request.Request(self.url, data=data, headers=headers,
                                             method="POST")
            t0 = time.monotonic()
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:1000]
                if exc.code not in RETRY_STATUSES or attempt == self.max_retries:
                    raise RuntimeError(f"HTTP {exc.code} from {self.url}: {detail}") from exc
                time.sleep(self.backoff_s * 2 ** attempt)
                continue
            if not isinstance(payload, Mapping):
                raise ValueError("response body is not a JSON object")
            return payload, time.monotonic() - t0
        raise AssertionError("unreachable")


def add_client_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--base-url", default=os.environ.get(BASE_URL_ENV, DEFAULT_BASE_URL),
        help=f"OpenAI-compatible API base URL (env {BASE_URL_ENV}, "
             f"default {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--api-key-env", default=API_KEY_ENV,
        help=f"name of the environment variable holding the API key (default {API_KEY_ENV})",
    )
    parser.add_argument(
        "--model", default=os.environ.get(MODEL_ENV),
        help=f"model identifier sent in the request (env {MODEL_ENV})",
    )
    parser.add_argument("--max-tokens", type=int, default=None,
                        help="max_tokens request field (omitted when unset)")
    parser.add_argument("--timeout", type=float, default=120.0,
                        help="per-request timeout in seconds")
    parser.add_argument(
        "--extra-body", default="{}",
        help='JSON object merged into every request body, e.g. \'{"reasoning_effort": "high"}\'',
    )


def client_from_args(args: argparse.Namespace) -> ChatClient:
    if not args.model:
        raise SystemExit(f"--model is required (or set {MODEL_ENV})")
    api_key = os.environ.get(args.api_key_env)
    if not api_key:
        raise SystemExit(f"environment variable {args.api_key_env} is not set")
    extra_body = json.loads(args.extra_body)
    if not isinstance(extra_body, dict):
        raise SystemExit("--extra-body must be a JSON object")
    return ChatClient(
        args.base_url, api_key, args.model, max_tokens=args.max_tokens,
        timeout=args.timeout, extra_body=extra_body,
    )
