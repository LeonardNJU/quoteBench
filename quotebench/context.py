"""QuoteBench-L2: deterministic realistic filler context for the long-context
track. The SAME 56 core tasks are evaluated with N tokens of prior "session"
context prepended, to measure whether quoting skill degrades as context grows
(the isolation and the contract-collapse effect held fixed).

The filler must be (a) deterministic/reproducible, (b) realistic (looks like an
agent's real working context — shell history, code, file listings, docs), and
(c) neutral: it must NOT contain a solution to any task nor quoting hazards that
would bias the measured skill. It is generated from a fixed corpus, tiled and
truncated to a token budget (~4 chars/token).
"""

from __future__ import annotations

# Neutral, realistic building blocks — plain shell/code/doc lines with NO
# guillemets, NO tricky quoting, NO task payloads. Deterministic order.
_SHELL = [
    "$ git status",
    "On branch main. Your branch is up to date with origin/main.",
    "$ ls -la src/",
    "total 48",
    "drwxr-xr-x  6 dev staff  192 Jan  3 10:12 .",
    "-rw-r--r--  1 dev staff 1284 Jan  3 09:58 main.py",
    "$ python -m pytest tests/ -q",
    "24 passed in 3.41s",
    "$ pip install -r requirements.txt",
    "Requirement already satisfied: numpy in ./venv/lib/python3.11/site-packages",
    "$ docker build -t app .",
    "Successfully tagged app:latest",
    "$ kubectl get pods",
    "NAME       READY   STATUS    RESTARTS   AGE",
    "web-7d9f   1/1     Running   0          2d",
]
_CODE = [
    "def load_config(path):",
    "    with open(path) as f:",
    "        return json.load(f)",
    "class Runner:",
    "    def __init__(self, cfg):",
    "        self.cfg = cfg",
    "        self.results = []",
    "    def step(self, x):",
    "        return self.model(x)",
    "for i, item in enumerate(items):",
    "    total += item.value",
    "    if total > threshold:",
    "        break",
    "logger.info('starting run with %d items', len(items))",
    "return sorted(out, key=lambda r: r.score, reverse=True)",
]
_DOC = [
    "The training loop iterates over the dataset for a fixed number of epochs.",
    "Each batch is moved to the device before the forward pass is computed.",
    "Gradients are accumulated over several micro-batches to fit memory limits.",
    "The learning rate follows a cosine schedule with a short linear warmup.",
    "Checkpoints are written every thousand steps to a deterministic path.",
    "Evaluation runs on a held-out split and reports the mean over three seeds.",
    "The service exposes a health endpoint and a metrics endpoint on separate ports.",
    "Requests are validated against a schema before they reach the handler.",
    "Rate limiting is applied per client to keep the shared backend responsive.",
    "Logs are structured as one JSON object per line for downstream ingestion.",
]

_CORPUS = []
# interleave the three registers so the filler reads like a mixed working session
for i in range(max(len(_SHELL), len(_CODE), len(_DOC))):
    for src in (_SHELL, _CODE, _DOC):
        if i < len(src):
            _CORPUS.append(src[i])


def make_context(n_tokens: int) -> str:
    """Return ~n_tokens of deterministic realistic filler (≈4 chars/token)."""
    if n_tokens <= 0:
        return ""
    budget = n_tokens * 4  # rough chars/token
    out, size, i = [], 0, 0
    # number the lines so a long context is not a single repeated block
    while size < budget:
        line = f"[{i:05d}] {_CORPUS[i % len(_CORPUS)]}"
        out.append(line)
        size += len(line) + 1
        i += 1
    return "\n".join(out)


def wrap_instruction(instruction: str, n_tokens: int) -> str:
    """Prepend N tokens of session context, task instruction last (haystack)."""
    if n_tokens <= 0:
        return instruction
    ctx = make_context(n_tokens)
    return (
        "The following is prior context from the current working session. Most of "
        "it is not relevant to your task; read what you need and ignore the rest.\n"
        "=== BEGIN SESSION CONTEXT ===\n"
        f"{ctx}\n"
        "=== END SESSION CONTEXT ===\n\n"
        "Now, your actual task (this is the only thing you must do):\n"
        f"{instruction}"
    )
