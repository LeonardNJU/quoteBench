# QuoteBench Spec (living doc)

## 1. What it measures

**Claim under test:** LLM agents systematically fail at shell quoting/escaping when
using shell tools directly (`bash -c` payloads), and compensate by writing temp
script files or falling back to non-shell tools. No existing benchmark isolates
this skill (see `relatedwork.md`).

QuoteBench measures the ability to compose **one correct `bash -c` payload** for a
task whose *only* real difficulty is quoting — the underlying operations (write a
file, replace a string, count lines, rename, commit) are trivial.

**Headline metric — Quoting Gap:** every scenario has a tier-0 control instance
(benign payload) and tier-1..3 hostile instances (increasingly hostile
metacharacters). Since the operation is identical within a scenario,

```
QuotingGap = pass(tier 0) − pass(tier 1-3)
```

attributes the performance drop purely to quoting skill, separating "can't quote"
from "can't use sed". Secondary metrics: first-attempt failure-class distribution
(shell-syntax / tool-error / runtime-error / **silent-wrong** / timeout /
environment-invalid),
and pass^k across trials (tau-bench style; quoting failures are tail events).
(Multi-turn feedback/repair is an agent-framework concern, not the benchmark's:
QuoteBench measures single-shot quoting. The harness has an optional retry mode
for exploration, but it is not a reported benchmark metric.)

**silent-wrong** (exit 0 but wrong final state) is tracked separately because it
is the dangerous case: no error signal for the agent loop to react to. The naive
baseline shows 22/36 hostile failures are silent-wrong.

**Multi-dimensional metrics (v0.2.1).** Beyond pass/fail, every API-adapter run
records per-task **token usage** (prompt / completion / reasoning / total,
normalized across OpenAI/Azure/Gemini/Anthropic usage shapes) and **latency**,
stored in each record's `usage_total`/`latency_total` and per-attempt. `score`
reports mean tokens/task, run totals, latency p50/p95, and — with
`QB_PRICE_IN`/`QB_PRICE_OUT` ($/1M) — a grounded cost estimate (per-run,
per-task, per-full-pass). This turns the "quoting accuracy vs thinking-token
budget" tradeoff into a measured curve (a non-saturated model can trade a large
jump in completion tokens for a meaningful accuracy gain, while a saturated
frontier model spends the extra tokens for ~no accuracy change). `MARKER-LEAKAGE` (⟪⟫
delimiter comprehension) is a separate reported dimension (I13). Static/offline
adapters expose no usage; those fields stay empty and the section is skipped.
Adapter-side reply cleaning (outer whitespace, markdown fences, `$ ` prompts,
or `<think>` wrappers) is recorded as `reply_cleanup` and reported separately:
default runs preserve compatibility with existing provider/adapter wrappers, while
`--strict-replies` turns any required cleanup into a `contract-error`.

## 2. Task model

A task = (fixture, instruction, validator, oracle, naive).

- **Fixture** — built purely from Python (`os` calls), so the bench itself never
  has a quoting problem. Deterministic; no timestamps/randomness.
- **Instruction** — natural language; exact literal payloads are delimited by
  `⟪…⟫` markers. Convention (stated in the system prompt): every character
  between markers is literal; `\` + letter is two characters; real line breaks
  appear as real line breaks. Tab-containing payloads are excluded in v0.1
  (display ambiguity would be a validity threat).
- **Validator** — state-based only (final file bytes, argv received by a probe
  program, `git log` output, parsed JSON). Never matches the agent's command
  string; any correct quoting strategy passes.
- **Environment contract** — core tasks declare the final set of files they are
  allowed to create. A command that reaches the target state but leaves any
  non-whitelisted extra file (for example a helper script or a local file when
  the task asks for a remote-side file) fails with `environment-invalid` and
  receives zero credit.
- **Oracle** — machine-constructed known-good command (via `shlex.quote` +
  `shellesc.py` sed escapers). Proves single-command solvability; `validate`
  requires 56/56 oracle pass on every platform change for the frozen core.
- **Naive** — the payload dropped into the *most tempting wrong* quoting for that
  payload class (single quotes for apostrophe-only payloads, double quotes
  otherwise; see `naive_wrap`). Raw validation requires: naive passes ALL controls
  and passes ZERO hostile instances. This is the attribution-clean
  discrimination proof — a raw hostile task a naive command passes is measuring
  nothing. Under `json`/`wrapped`, the contract layer itself can make controls
  hard, so contract validation is interpreted as oracle-solvability plus
  hostile-naive sanity, not as a clean Quoting Gap proof.

## 3. Scenario × hazard matrix (14 scenarios × 4 tiers = 56 tasks)

> v0.2 additions: `heredoc-write` and `ssh-heredoc`; **contracts** (§5b) as a
> second experimental axis; pass^k trials. Frozen 2026-07-11.

| scenario | operation | hazard axis (ShellCheck anchor) |
|---|---|---|
| write-file | create file with exact content | quote choice, `$`/backtick in content (SC2016) |
| sed-replace | literal string replace | shell quoting × sed BRE/replacement escaping (two layers) |
| grep-count | count exact-substring lines | regex-vs-literal (`-F`), `$` expansion |
| field-lookup | awk column lookup | `$1` shell-vs-awk collision, awk `-v` escape processing |
| hostile-filenames | rename/delete one file | spaces, glob-lookalike names, leading dash (option injection), collateral damage |
| find-glob | list matching files | unquoted `-name` pattern + cwd trap file (SC2061) |
| ssh-nested | create file through ssh-like wrapper | double shell evaluation, client-side expansion (SC2029) |
| json-write | write JSON with exact value | JSON escaping × shell quoting |
| git-commit | commit with exact message | apostrophes, multiline `-m`, backtick substitution |
| env-passing | run prog with exact env var | assignment quoting, `$`, multiline value |
| argv-passing | pass 3 exact argv strings | word splitting (SC2086), glob expansion, `-n` leading dash |
| bulk-rename | prefix all *.txt | unquoted `"$f"` in loop (SC2086), hostile names |
| heredoc-write | create exact multiline file | unquoted-delimiter expansion (SC2087), delimiter collision (bare `EOF` line in content) |
| ssh-heredoc | create exact multiline file through ssh-like wrapper | nested heredoc quoting, remote-side expansion, delimiter collision |

Tiers: t0 benign control → t1 single hazard (usually apostrophe/space) → t2
different hazard family → t3 combination. Hazard tags on every task enable
per-hazard analysis.

## 4. Harness

- Execution: fresh temp dir per attempt → fixture → `execve(["bash","-c",cmd])`
  (no extra shell layer) → validator. Trimmed env (`HOME`=taskdir, `LC_ALL=C`,
  `GIT_CONFIG_NOSYSTEM=1`), 15 s timeout.
- Executors: `local` (default; fine for oracle/naive) and `docker`
  (`--network none`, bind-mounted task dir; pins a GNU userland and removes
  network access, but is not a formal security boundary — see `Dockerfile`).
- Mode: `oneshot` (one command, strict) is the benchmark. An optional `retry`
  mode exists in the harness for exploration only; it is not part of the reported
  metrics (multi-turn repair is an agent-framework concern, out of scope).
- The one-command format is enforced by final-state auditing: each core task
  declares an `allowed_new_files` set, and any non-whitelisted persistent helper
  script or stray output is `environment-invalid` with zero credit.

## 5b. Contracts (harness-realism axis, v0.2)

How the command is elicited/transported — same tasks, same rules text, only the
transport differs (`--contract`):
- `raw` — reply executed verbatim via `bash -c`.
- `json` — reply must be `{"command": "..."}`; strict-parsed, then executed.
  Mirrors real tool-calling (JSON string escaping composes with shell quoting).
  Parse failure = `contract-error`.
- `wrapped` — reply R is interpolated into `bash -c "R"` and that string is
  executed. Mirrors naive harness interpolation (codex#20875). Oracle transform:
  `dq_embed_escape` (escape `\ " $ \``).
`validate --contract X` requires the transformed oracles to pass 56/56 —
contracts are provably solvable. Caveat: under `json`/`wrapped`, controls carry
intrinsic transport difficulty, so the Quoting Gap loses attribution purity —
compare contract DELTAS there instead.

## 5. Adapters

`oracle` / `naive` (static), `anthropic` (API), `openai` (any
/v1/chat/completions endpoint via `OPENAI_BASE_URL` — vLLM serving included),
`cmd` (pipe prompt to an external command adapter), `file` (offline JSONL of
task_id→command).

## 6. Validity checks (run before trusting any number)

`python -m quotebench validate` enforces:
1. oracle 56/56 (single-command solvability, portability of gold commands);
2. naive passes 0 hostile tasks (discrimination);
3. under the raw contract, naive passes all 14 controls (controls stay easy —
   otherwise the raw Quoting Gap conflates task difficulty with quoting
   difficulty).

Sampling caveat (cross-model comparability): reasoning models reject an explicit
temperature, so runs use each provider's most-deterministic available setting —
provider default sampling when temperature is unsupported, Azure reasoning models
with temperature omitted, Gemini flash at temperature 0. Per-model ABSOLUTE
numbers therefore carry a sampling confound; the clean signals are WITHIN-model contract deltas
(raw/json/wrapped) and effort deltas, which hold sampling fixed. Cross-vendor
absolute rankings should be read as coarse tiers, not fine ordering.

Known validity notes:
- Gold-command quality matters (NL2SH-ALFA found >50% errors in InterCode's gold
  data); here oracles are machine-constructed from audited escapers and
  execution-verified, not hand-written.
- macOS (BSD tools, bash 3.2) vs Linux (GNU): oracles restricted to portable
  constructs; full evals should use `--executor docker` for a pinned toolchain.
- grep-count decoys are chosen so regex-count ≠ literal-count (a discovered
  coincidence class: as a regex, `a.b*c` does NOT match the literal text
  `a.b*c`, so decoy/literal counts can silently tie).

## 7. Roadmap (v0.2+)

- Package as Terminal-Bench/Harbor task set → instant compatibility with
  existing agent harnesses (theirs measures end-to-end agents; ours stays the
  isolation probe).
- Agentic mode with a real multi-turn shell + temp-script *detection* (score the
  avoidance behavior itself, not just forbid it).
- Payload × scenario cross-product generator for a large training split
  (parameterized generation is already the architecture; 56 eval tasks are the
  frozen, human-audited core).
- Tab/control-char payloads once an unambiguous instruction encoding is chosen;
  non-UTF-8 filename tier (Wheeler's hardest class); PowerShell/cmd.exe track
  (cross-shell wrapping is a top real-world failure per codex#7298).
