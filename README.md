# QuoteBench

**How matched scores can hide command-path failures.** QuoteBench is an
execution-verified benchmark for whether one-shot Bash programs preserve literal
intent across generation contracts and downstream command transport.

Code, tasks, validators, and the rollout archive for double-blind review. Links to the paper, project page, and hosted dataset are withheld.


To evaluate your own model, follow [docs/RUN_YOUR_MODEL.md](docs/RUN_YOUR_MODEL.md): two `run` commands (raw and disclosed-boundary contract) and one `crossover` command print RR, RN, NR, NN, damage, and compensation.

## What QuoteBench measures

The frozen core contains **56 tasks across 14 operation families**. Each task is
scored by exact final state—file bytes, argv, JSON, directory contents, or Git
history—not by command-string matching. The validator audit rejects **197/197**
applicable state mutations.

For the mechanism study, QuoteBench crosses two generation contracts (raw and a
single-sentence disclosed-boundary contract) with two execution transports (raw and
one added double-quoted parser). Fixed-reply replay separates damage introduced after
generation from compensation learned under the disclosed contract.

## Main findings

- The added parser lowers fixed-reply success by **55.4–73.2 percentage points** in
  all eight same-window configurations.
- Six of eight configurations recover **30.4–60.7 points** through
  contract-conditioned compensation.
- **The two matched command paths order models differently** (Kendall rank
  correlation 0.57, task-cluster bootstrap interval [0.32, 0.82]). One reversal
  among 26 comparable pairs is unambiguous; the others sit on
  single-task margins.
- Three public draws for all eight same-window configurations keep damage negative
  in every configuration and draw and change no compensation sign.
- Compensation trained for the targeted wrapper does not transfer reliably to a
  single-quoted or double-nested wrapper.
- Executing stored replies as temporary scripts recovers **50.0–78.6 points** without
  changing the model output.

### Four-cell same-window decomposition

| model | RR | RN | NR | NN | damage | compensation | matched gap |
|---|---|---|---|---|---|---|---|
| GPT-5.6-sol | 94.6% | 30.4% | 55.4% | 91.1% | -64.3 | +60.7 | -3.6 |
| GPT-5.5 | 100.0% | 28.6% | 50.0% | 89.3% | -71.4 | +60.7 | -10.7 |
| Opus-5 | 96.4% | 30.4% | 42.9% | 89.3% | -66.1 | +58.9 | -7.1 |
| Gemini-3.1-Pro | 98.2% | 25.0% | 33.9% | 80.4% | -73.2 | +55.4 | -17.9 |
| Gemini-3.5-Flash | 96.4% | 28.6% | 67.9% | 58.9% | -67.9 | +30.4 | -37.5 |
| Opus-4.8 | 91.1% | 26.8% | 62.5% | 57.1% | -64.3 | +30.4 | -33.9 |
| Qwen3.5-27B | 85.7% | 30.4% | 83.9% | 30.4% | -55.4 | +0.0 | -55.4 |
| Gemini-3.1-Flash-Lite | 78.6% | 19.6% | 80.4% | 14.3% | -58.9 | -5.4 | -64.3 |

`RR` and `NN` are matched paths. `RN − RR` is fixed-reply transport damage;
`NN − RN` is the realized contract-conditioned contrast. Values are final-state
success on the 56-task GNU replay.

### Boundary-removal mitigation

| model | nested wrapper | temporary script | gain |
|---|---|---|---|
| GPT-5.6-sol | 8/42 | 41/42 | +78.6 pp |
| Opus-4.8 | 7/42 | 40/42 | +78.6 pp |
| Qwen3.5-27B | 9/42 | 30/42 | +50.0 pp |

The typed-operation experiment is an **exploratory two-model pilot** over six
naturally typeable families. It is not presented as a universal mitigation: typed
representations can remove shell parsing while introducing representation errors.

## Public rollout release

The dataset contains **12,999 model-generation rollouts in
33 arm files** across four active campaigns. One generation is one
rollout; every replay of its exact stored reply is attached under `executions`.
The bundle includes `rollout-schema.json`, `MANIFEST.json`, and `SHA256SUMS`.

## Validate and score locally

```bash
docker build -t quotebench-runner .
python3 -m quotebench validate --executor docker
python3 -m quotebench score \
  --input generations.jsonl \
  --out scored.jsonl \
  --executor docker
```

Use the network-disabled Docker executor for untrusted generations. Fresh model
access is not required to validate the benchmark or replay the released evidence.

## Scope

QuoteBench is a finite diagnostic for POSIX/Bash command construction. It does not
estimate deployment prevalence and does not cover PowerShell, Windows CMD,
authentication, network failures, interactive terminal state, or multi-turn recovery.

## License and citation

Released under Apache-2.0. Citation metadata is in `CITATION.cff`.
