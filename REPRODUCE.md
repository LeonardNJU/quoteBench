# Reproducing QuoteBench

QuoteBench is pure Python 3 (stdlib only for the core; `matplotlib` only for
figures). No install step. All commands run from the repo root.

## 0. Sanity: the benchmark is internally valid

```bash
python3 -m quotebench validate                 # raw: oracle 56/56 + clean naive discrimination
python3 -m quotebench validate --contract json # oracle 56/56 under JSON transport
python3 -m quotebench validate --contract wrapped # oracle 56/56 under wrapped transport
```
Each must print `oracle: 56/56 passed` and `discrimination: naive command passes
0 hostile task(s) (good)`. The raw run should also have no failed controls. For
`json`/`wrapped`, the contract layer can itself make naive controls fail; those
runs prove contract oracle-solvability and hostile-naive sanity, while the
attribution-clean Quoting Gap proof is the raw run.

## 1. Inspect / export tasks

```bash
python3 -m quotebench list                      # 56 tasks, tiers, hazard tags
python3 -m quotebench show ssh-heredoc/t3-gnarly
python3 -m quotebench export --out tasks/manifest.jsonl
```

## 2. Toolchain (important — read this)

Results depend on the shell toolchain. `--executor local` uses the host's
`sed/grep/awk` (BSD on macOS, GNU on Linux); `--executor docker` uses a pinned
GNU image (`docker build -t quotebench-runner .`). A correctly-quoted GNU idiom
(`sed -i '…'`) fails on BSD and vice-versa, so **report the toolchain**. The
paper's reference numbers are GNU (docker). Use docker for any untrusted
open-weight model output.

## 3. Evaluate a model

```bash
# Any OpenAI-compatible endpoint, including local vLLM serving: --adapter openai
OPENAI_BASE_URL=http://localhost:8000/v1 OPENAI_API_KEY=EMPTY \
  python3 -m quotebench run --adapter openai --model MODEL \
  --contract raw --parallel 4 --executor docker --out results/run.jsonl

# Azure reasoning models (effort ladder): --adapter azure --reasoning-effort high
# Google Gemini API:                       --adapter gemini
# External command adapter:                --adapter cmd --adapter-cmd 'COMMAND'
# Baselines:                               --adapter oracle | naive
```
Flags: `--trials N` (pass^k), `--mode retry --max-attempts K` (recovery),
`--min-interval S` (RPM guard), `--resume` (checkpoint-safe), `--tasks FILTER`.

## 4. Score (pass@1 + Wilson CI + failure taxonomy + tokens/cost)

```bash
python3 -m quotebench score results/run.jsonl
QB_PRICE_IN=1.25 QB_PRICE_OUT=10 python3 -m quotebench score results/run.jsonl   # add $ cost
```
Reports overall/control/hostile with 95% Wilson CI, Quoting Gap, per-scenario and
per-tier breakdowns, first-attempt failure classes, and — separated as their own
lines — `MARKER-LEAKAGE` (⟪⟫ comprehension, not quoting) and `DIALECT` (GNU/BSD
idiom mismatch, not quoting), plus token/latency/cost when the adapter reports
usage.

If a passing final state leaves non-whitelisted extra files, the run is marked
failed with `environment-invalid` and receives zero credit. For partial
multi-trial files, `score` reports observed all-pass across available trials
rather than pretending the file supports a complete `pass^k`; no missing trials
are imputed or run.

## 5. Replay BSD results on GNU (no model re-calls)

```bash
python3 -m quotebench replay results/run.jsonl --executor docker --out results/run-gnu.jsonl
```
Re-executes every stored attempt under a different toolchain — gives GNU numbers
for free from a BSD run (or vice-versa) without losing retry/failure taxonomy.

## 6. Figures

The public repository includes the current headline figures under
`docs/paper/figures/`.

## Determinism / notes

Fixtures use MD5-derived tokens (no timestamps/randomness); re-runs are
byte-identical. Reasoning models reject an explicit temperature, so each runs at
its provider default (a sampling confound for cross-vendor *absolute* numbers;
within-model contract/effort deltas hold it fixed). See `docs/SPEC.md` for design
and the figures in `docs/paper/figures/` for the headline analysis. The Docker
executor pins a GNU userland and disables networking, but it is not a formal
security boundary.
