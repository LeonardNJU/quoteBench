# QuoteBench

A benchmark that isolates **shell quoting/escaping skill** in LLM agents: 14
scenarios × 4 hostility tiers = 56 execution-verified tasks, each solvable by a
single `bash -c` command, evaluated under 3 tool contracts (`raw` / `json` /
`wrapped`) and pass^k reliability trials. Headline metrics: **Quoting Gap**
(benign-control − hostile pass rate; attribution-clean under raw),
**contract drop** (raw − wrapped), and **reliability gap** (per-trial − pass^k).

This file is a map; facts live in `docs/`:

- design & metrics → `docs/SPEC.md`
- novelty & prior art → `docs/relatedwork.md`
- dataset card → `docs/paper/datasheet.md`
- license → `LICENSE`
- citation metadata → `CITATION.cff`

The public repository and project site are synchronized from the private release
source by GitHub Actions.

## Results preview

QuoteBench exposes failures that are easy to miss in broad coding benchmarks:

- Some models are strong under direct shell execution but collapse when the same
  command is transported through a wrapped harness. In the frozen-core GNU run,
  Gemini-3.5-flash scores 96.4% under `raw` but 67.9% under `wrapped`.
- Reasoning budget is not a universal fix: saturated models spend more tokens
  without improving accuracy, while mid-capability models can convert effort
  into large gains.
- pass@1 overstates reliability for tail-risk skills. The scorer reports
  pass^k/observed all-pass separately when repeated trials are available.

![Raw vs wrapped contract collapse](docs/paper/figures/fig1_contract_collapse.png)

![Accuracy vs generated-token cost](docs/paper/figures/fig2_effort_saturation.png)

![Qwen thinking toggle size gradient](docs/paper/figures/fig3_size_gradient.png)

![pass@1 vs pass^k reliability](docs/paper/figures/fig4_reliability.png)

## Quickstart

```bash
python3 -m quotebench list                 # enumerate 56 frozen-core tasks
python3 -m quotebench show ssh-heredoc/t3-gnarly
python3 -m quotebench validate             # oracle 56/56 + discrimination proof
python3 -m quotebench run --adapter naive --out results/naive.jsonl
OPENAI_BASE_URL=http://localhost:8000/v1 OPENAI_API_KEY=... \
    python3 -m quotebench run --adapter openai --model qwen3.5-9b \
    --contract wrapped --executor docker --out results/qwen.jsonl
python3 -m quotebench run --adapter azure --model YOUR_DEPLOYMENT_NAME \
    --contract wrapped --executor docker --out results/gpt.jsonl
python3 -m quotebench score results/qwen.jsonl
```

No dependencies for the core benchmark (Python 3 stdlib). All benchmark jobs are
`python3 -m quotebench` subcommands (see `quotebench/cli.py`).
For untrusted model output use `--executor docker` (build with `docker build
-t quotebench-runner .`).

## License and citation

QuoteBench is released under the Apache License 2.0. Redistribution must retain
the copyright, license, and NOTICE attribution. If you use QuoteBench in
research, reporting, evaluation, derivative benchmarks, or public comparisons,
please cite the repository metadata in `CITATION.cff`.
