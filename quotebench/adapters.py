"""Model adapters. An adapter maps a message list -> one bash command string.

Messages are [{"role": "user"|"assistant", "content": str}, ...]; the system
prompt (core.SYSTEM_PROMPT) is passed separately where the API supports it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from .core import SYSTEM_PROMPT


@dataclass
class Gen:
    """An adapter's reply plus per-call metrics (None usage = provider gave
    none, e.g. an offline adapter). token counts are normalized across providers."""
    text: str
    usage: dict = field(default_factory=dict)  # prompt/completion/reasoning/total
    latency: float = 0.0
    raw_text: str = ""
    cleanup: list = field(default_factory=list)


def _usage_openai(u: dict) -> dict:
    if not u:
        return {}
    det = u.get("completion_tokens_details") or {}
    return {
        "prompt": u.get("prompt_tokens", 0),
        "completion": u.get("completion_tokens", 0),
        "reasoning": det.get("reasoning_tokens", 0),
        "total": u.get("total_tokens", 0),
    }


def _usage_gemini(u: dict) -> dict:
    if not u:
        return {}
    return {
        "prompt": u.get("promptTokenCount", 0),
        "completion": u.get("candidatesTokenCount", 0),
        "reasoning": u.get("thoughtsTokenCount", 0),
        "total": u.get("totalTokenCount", 0),
    }


def _usage_anthropic(u: dict) -> dict:
    if not u:
        return {}
    return {
        "prompt": u.get("input_tokens", 0),
        "completion": u.get("output_tokens", 0),
        "reasoning": 0,  # folded into output_tokens in the messages API
        "total": u.get("input_tokens", 0) + u.get("output_tokens", 0),
    }


class _Throttle:
    """Global min-interval between calls (thread-safe) — for low-RPM APIs."""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        while True:
            with self._lock:
                now = time.monotonic()
                if now >= self._next:
                    self._next = now + self.min_interval
                    return
                delay = self._next - now
            time.sleep(min(delay, 1.0))

_FENCE_RE = re.compile(r"^```[a-zA-Z]*\n(.*?)\n?```\s*$", re.DOTALL)


def clean_reply_with_meta(text: str) -> tuple[str, list]:
    """Strip adapter/model wrappers while recording every transformation."""
    cleanup = []
    t = text
    stripped = t.strip()
    if stripped != t:
        cleanup.append("outer-whitespace")
    t = stripped
    # reasoning models (qwen3, etc.) wrap CoT in <think>...</think> before the
    # answer — keep only what follows the last close tag
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


def clean_reply(text: str) -> str:
    """Compatibility wrapper for callers that only need the cleaned text."""
    return clean_reply_with_meta(text)[0]


def _gen_from_text(text: str, usage: dict, latency: float) -> Gen:
    cleaned, cleanup = clean_reply_with_meta(text)
    return Gen(cleaned, usage, latency, raw_text=text, cleanup=cleanup)


class AnthropicAdapter:
    def __init__(self, model: str, max_tokens: int = 1024):
        self.model = model
        self.max_tokens = max_tokens
        self.key = os.environ.get("ANTHROPIC_API_KEY")
        if not self.key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")

    def generate(self, messages: list, system_prompt: str = SYSTEM_PROMPT) -> Gen:
        body = json.dumps({
            "model": self.model, "max_tokens": self.max_tokens,
            "system": system_prompt, "messages": messages, "temperature": 0,
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=body,
            headers={"x-api-key": self.key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"})
        t0 = time.monotonic()
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
        text = "".join(b.get("text", "") for b in data.get("content", []))
        return _gen_from_text(text, _usage_anthropic(data.get("usage", {})),
                              time.monotonic() - t0)


class OpenAIAdapter:
    """Works with any OpenAI-compatible /v1/chat/completions endpoint
    (set OPENAI_BASE_URL for vLLM/other providers)."""

    def __init__(self, model: str, max_tokens: int = 4096):
        self.model = model
        self.max_tokens = max_tokens
        self.key = os.environ.get("OPENAI_API_KEY", "EMPTY")
        self.base = os.environ.get(
            "OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        # optional extra request fields (e.g. vLLM chat_template_kwargs to turn
        # off qwen thinking): QB_OPENAI_EXTRA_BODY='{"chat_template_kwargs":...}'
        self.extra = json.loads(os.environ.get("QB_OPENAI_EXTRA_BODY", "{}"))

    def generate(self, messages: list, system_prompt: str = SYSTEM_PROMPT) -> Gen:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system_prompt}] + messages,
            "max_tokens": self.max_tokens, "temperature": 0,
        }
        payload.update(self.extra)
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.base}/chat/completions", data=body,
            headers={"Authorization": f"Bearer {self.key}",
                     "content-type": "application/json"})
        t0 = time.monotonic()
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.loads(r.read())
        text = data["choices"][0]["message"]["content"] or ""
        return _gen_from_text(text, _usage_openai(data.get("usage", {})),
                              time.monotonic() - t0)


class AzureOpenAIAdapter:
    """Azure OpenAI chat completions (reasoning models: gpt-5.5/5.6 use
    max_completion_tokens; temperature omitted — reasoning models reject it).
    Env: AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, AZURE_OPENAI_API_VERSION."""

    def __init__(self, deployment: str, max_tokens: int = 8192,
                 reasoning_effort: str = "", min_interval: float = 0.0):
        endpoint = os.environ["AZURE_OPENAI_ENDPOINT"].rstrip("/")
        ver = os.environ.get("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
        self.url = (f"{endpoint}/openai/deployments/{deployment}"
                    f"/chat/completions?api-version={ver}")
        self.key = os.environ["AZURE_OPENAI_KEY"]
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort
        self.throttle = _Throttle(min_interval)

    def generate(self, messages: list, system_prompt: str = SYSTEM_PROMPT) -> Gen:
        body = {
            "messages": [{"role": "system", "content": system_prompt}] + messages,
            "max_completion_tokens": self.max_tokens,
        }
        if self.reasoning_effort:
            body["reasoning_effort"] = self.reasoning_effort
        t0 = time.monotonic()
        data = _post_json(self.url, body, {"api-key": self.key}, self.throttle)
        latency = time.monotonic() - t0
        choice = data["choices"][0]
        content = choice["message"].get("content")
        finish = choice.get("finish_reason")
        # a reasoning model that spends the whole budget on reasoning returns
        # null/empty content with finish_reason=length — that is an adapter
        # failure, NOT an empty command that should execute "" and score as a
        # normal task failure (MUST-FIX 1/2)
        if not content or (content or "").strip() == "":
            raise RuntimeError(
                f"azure: empty content (finish_reason={finish}) — likely "
                "reasoning consumed max_completion_tokens")
        if finish not in (None, "stop"):
            raise RuntimeError(f"azure: non-stop finish_reason={finish} — "
                               "possibly truncated command")
        return _gen_from_text(content, _usage_openai(data.get("usage", {})),
                              latency)


class GeminiAdapter:
    """Google Generative Language API (AI Studio key). 429-aware with
    retryDelay parsing; use min_interval to stay under low RPM (pro tier).
    Env: GEMINI_API_KEY."""

    def __init__(self, model: str, max_tokens: int = 8192,
                 min_interval: float = 0.0, thinking_level: str = ""):
        self.url = ("https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{model}:generateContent")
        self.key = os.environ["GEMINI_API_KEY"]
        self.thinking_level = thinking_level
        self.max_tokens = max_tokens
        self.throttle = _Throttle(min_interval)

    def generate(self, messages: list, system_prompt: str = SYSTEM_PROMPT) -> Gen:
        contents = [{"role": "model" if m["role"] == "assistant" else "user",
                     "parts": [{"text": m["content"]}]} for m in messages]
        gen_cfg = {"temperature": 0, "maxOutputTokens": self.max_tokens}
        if self.thinking_level:
            gen_cfg["thinkingConfig"] = {"thinkingLevel": self.thinking_level}
        body = {
            "system_instruction": {"parts": [{"text": system_prompt}]},
            "contents": contents,
            "generationConfig": gen_cfg,
        }
        t0 = time.monotonic()
        data = _post_json(self.url, body, {"x-goog-api-key": self.key},
                          self.throttle)
        latency = time.monotonic() - t0
        try:
            cand = data["candidates"][0]
        except (KeyError, IndexError):
            raise RuntimeError(f"gemini: no candidates: {json.dumps(data)[:300]}")
        finish = cand.get("finishReason")
        # MAX_TOKENS / SAFETY / RECITATION → truncated or withheld output; must
        # be an adapter error, not a truncated command executed as normal
        # (MUST-FIX 2)
        if finish not in (None, "STOP"):
            raise RuntimeError(f"gemini: finishReason={finish} — truncated/blocked")
        parts = cand.get("content", {}).get("parts", [])
        text = "".join(p["text"] for p in parts if "text" in p)
        if not text.strip():
            raise RuntimeError(
                f"gemini: no text parts (finishReason={finish}, "
                f"parts={json.dumps(parts)[:200]})")
        return _gen_from_text(text, _usage_gemini(data.get("usageMetadata", {})),
                              latency)


def _post_json(url: str, body: dict, headers: dict, throttle: _Throttle,
               max_retries: int = 5) -> dict:
    """POST with 429/5xx backoff (honors gemini retryDelay when present)."""
    payload = json.dumps(body).encode()
    hdrs = dict(headers, **{"content-type": "application/json"})
    for attempt in range(max_retries + 1):
        throttle.wait()
        req = urllib.request.Request(url, data=payload, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", "replace")
            if e.code in (429, 500, 502, 503, 504) and attempt < max_retries:
                delay = 15.0 * (attempt + 1)
                m = re.search(r'"retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', err_body)
                if m:
                    delay = max(delay, float(m.group(1)) + 1)
                time.sleep(delay)
                continue
            raise RuntimeError(f"HTTP {e.code}: {err_body[:300]}")


class CmdAdapter:
    """Pipes the full prompt to an arbitrary external command's stdin and reads
    the model reply from its stdout. Example:
        --adapter cmd --adapter-cmd 'COMMAND_THAT_PRINTS_ONE_COMMAND'
    The reply must be plain text containing one bash command.
    """

    def __init__(self, argv_str: str):
        self.argv_str = argv_str
        # If the wrapped command emits the supported JSON envelope, parse the
        # reply and usage.output_tokens instead of discarding usage. Detected by
        # the flag so plain-text command adapters are unaffected.
        self.json_out = "--output-format json" in argv_str

    def generate(self, messages: list, system_prompt: str = SYSTEM_PROMPT) -> Gen:
        parts = [f"[system]\n{system_prompt}"]
        for m in messages:
            parts.append(f"[{m['role']}]\n{m['content']}")
        prompt = "\n\n".join(parts)
        t0 = time.monotonic()
        r = subprocess.run(["bash", "-c", self.argv_str], input=prompt,
                           capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise RuntimeError(f"adapter command failed: {r.stderr[-500:]}")
        if self.json_out:
            try:
                data = json.loads(r.stdout)
            except json.JSONDecodeError:
                raise RuntimeError(f"command-adapter json parse failed: {r.stdout[:300]}")
            # Raise (not silently record) on any non-success so the runner's
            # circuit breaker trips on quota/limit errors — never execute a
            # "you've reached your limit" string as a command (lesson I9/I10).
            if data.get("is_error") or data.get("api_error_status") or \
                    data.get("subtype") != "success":
                raise RuntimeError(f"command-adapter non-success: {json.dumps(data)[:300]}")
            return _gen_from_text(data.get("result", ""),
                                  _usage_anthropic(data.get("usage", {}) or {}),
                                  time.monotonic() - t0)
        # plain-text command adapter (no --output-format json): reply only, no usage
        return _gen_from_text(r.stdout, {}, time.monotonic() - t0)


class AgyAdapter:
    """Drives an external Gemini command adapter in plan-only mode from a
    throwaway dir. Prompt is passed as a single argv element (no shell, no
    quoting hazard). Effort is baked into the model id. No token usage is
    reported by this adapter — latency only."""

    def __init__(self, model: str, agy_bin: str = ""):
        self.model = model
        self.bin = agy_bin or os.path.expanduser("~/.local/bin/agy")
        self.workdir = os.path.join(
            os.environ.get("TMPDIR", "/tmp"), "qb-agy-scratch")
        os.makedirs(self.workdir, exist_ok=True)

    def generate(self, messages: list, system_prompt: str = SYSTEM_PROMPT) -> Gen:
        parts = [system_prompt]
        for m in messages:
            parts.append(f"[{m['role']}]\n{m['content']}")
        prompt = "\n\n".join(parts)
        argv = [self.bin, "-p", prompt, "--model", self.model, "--mode", "plan"]
        t0 = time.monotonic()
        r = subprocess.run(argv, cwd=self.workdir, capture_output=True,
                           text=True, timeout=600)
        if r.returncode != 0:
            raise RuntimeError(f"agy failed: {r.stderr[-400:] or r.stdout[-400:]}")
        return _gen_from_text(r.stdout, {}, time.monotonic() - t0)


class FileAdapter:
    """Offline: reads {task_id: command} from a JSONL of
    {"task_id": ..., "command": ...}. Only supports one-shot mode."""

    def __init__(self, path: str):
        self.commands = {}
        with open(path) as f:
            for line in f:
                if line.strip():
                    rec = json.loads(line)
                    self.commands[rec["task_id"]] = rec["command"]

    def lookup(self, task_id: str) -> str:
        return self.commands[task_id]


def make_adapter(name: str, model: str = "", adapter_cmd: str = "",
                 file_path: str = "", min_interval: float = 0.0,
                 reasoning_effort: str = ""):
    if name == "anthropic":
        return AnthropicAdapter(model or "claude-haiku-4-5")
    if name == "openai":
        return OpenAIAdapter(model or "gpt-4o-mini")
    if name == "azure":
        return AzureOpenAIAdapter(model, reasoning_effort=reasoning_effort,
                                  min_interval=min_interval)
    if name == "gemini":
        return GeminiAdapter(model, min_interval=min_interval,
                             thinking_level=reasoning_effort)
    if name == "agy":
        return AgyAdapter(model)
    if name == "cmd":
        if not adapter_cmd:
            raise RuntimeError("--adapter cmd requires --adapter-cmd")
        return CmdAdapter(adapter_cmd)
    if name == "file":
        if not file_path:
            raise RuntimeError("--adapter file requires --file")
        return FileAdapter(file_path)
    raise RuntimeError(f"unknown adapter {name!r} "
                       "(oracle/naive are handled by the runner)")
