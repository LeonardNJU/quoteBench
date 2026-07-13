# Datasheet for QuoteBench

Following Gebru et al., "Datasheets for Datasets." QuoteBench is a
*programmatically generated, execution-verified* benchmark, not a scraped corpus;
several questions are answered in that light.

## Motivation

- **Why created.** To isolate and measure a specific, widely-reported LLM-agent
  failure — incorrect shell quoting/escaping when driving a shell tool directly —
  which no existing benchmark isolates (terminal/agentic benchmarks exercise it
  only incidentally; NL→command benchmarks use metrics that erase argument
  values where quoting lives).
- **Funding / creators.** Academic research. Author list and formal
  acknowledgements will be updated with the project paper.

## Composition

- **Instances.** 56 core tasks = 14 scenarios × 4 hostility tiers (t0 benign
  control + t1–t3 increasingly hostile). A separate v0.3 suite (12 tasks) is not
  part of the frozen core and is not released with the public core.
- **What each instance is.** A tuple: a Python-built filesystem *fixture*, a
  natural-language *instruction* with exact literal payloads delimited by `⟪⟫`,
  a state-based *validator*, a machine-constructed *oracle* command, and a
  *naive* (tempting-wrong) probe. No task contains personal data.
- **Labels / ground truth.** The oracle proves single-command solvability
  (validated 56/56 under all three contracts); ground truth is the final program
  *state*, computed in Python from the payload, not a reference command string.
- **Splits.** None — it is an evaluation-only benchmark (no train/test split).
- **Hazard tags.** Each task is tagged with the quoting hazards it exercises,
  anchored to ShellCheck rule families (SC2086, SC2016, SC2029, …) and Wheeler's
  hostile-filename classes.
- **Errors/noise/redundancy.** Oracles are machine-constructed and
  execution-verified (avoiding the >50% gold-command error rate reported for a
  prior hand-written benchmark). No duplicate task IDs (asserted at build).

## Collection process

- **How generated.** Entirely by Python code (`quotebench/scenarios.py`); the
  benchmark never invokes a shell to build fixtures, so it has no quoting problem
  of its own. Scenario *design* was seeded by a de-identified corpus of real
  quoting-failure incidents mined (with consent) from the authors' own coding-
  agent sessions; the released tasks are generic distillations, not verbatim
  private commands.
- **Determinism.** MD5-derived tokens, no timestamps/randomness; regeneration is
  byte-identical.

## Preprocessing / labeling

- None beyond generation. Validators are state-based and reject nothing on
  surface form.

## Uses

- **Intended.** Measuring and comparing LLMs'/agents' shell-quoting skill in
  isolation, under different tool contracts and reasoning-effort settings; study
  of harness-transport effects; a template for isolating other verifiable micro-
  skills (the "isolation recipe," paper §9).
- **Out of scope / caution.** Not a general shell-competence or end-to-end agent
  benchmark; absolute cross-vendor numbers carry a sampling confound (read as
  tiers); numbers are toolchain-dependent (report BSD vs GNU); single-trial
  pass@1 should be paired with the reported Wilson CIs (and pass^k where given).
- **Safety.** Some tasks use hostile filenames (leading dash, glob-lookalikes)
  and destructive-looking operations; evaluate untrusted (e.g. open-weight)
  model output under `--executor docker` (`--network none`, isolated tempdir).

## Distribution & maintenance

- **Release.** Public code repository (frozen 56-task core + harness + adapters +
  scorer + replay + Dockerfile), released under Apache-2.0 with NOTICE
  attribution and `CITATION.cff` metadata; anonymized mirror for double-blind
  review.
- **Versioning.** Core frozen at 56 tasks; extensions (v0.3 scenarios,
  PowerShell track, long-context QuoteBench-L2) are versioned separately so the
  core stays comparable across runs.
- **Contact / contributions.** Via the repository.
