# Datasheet for QuoteBench

Following Gebru et al., "Datasheets for Datasets." QuoteBench is a
*programmatically generated, execution-verified* benchmark, not a scraped corpus;
several questions are answered in that light.

## Motivation

- **Why created.** To isolate and measure a specific, widely-reported LLM-agent
  failure—loss of executable payload and argument semantics in one-shot Bash
  commands that cross a command boundary. Broad terminal benchmarks exercise these failures inside complete
  agents; QuoteBench makes the boundary a controlled experimental factor while
  holding task intent and final-state verification fixed.
- **Funding / creators.** Academic research by Shangao Li, Yao Zhang, Volker Tresp, and Yuanyuan
  Yang. API-access acknowledgements are listed in the current paper.

## Composition

- **Instances.** 56 core tasks = 14 scenarios × 4 payload variants (one benign
  control + three hazardous variants). A separate v0.3 suite (12 tasks) is not
  part of the frozen core and is not released with the public core.
- **What each instance is.** A tuple: a Python-built filesystem *fixture*, a
  natural-language *instruction* with exact literal payloads delimited by `⟪⟫`,
  a state-based *validator*, a machine-constructed *oracle* command, and a
  *naive* (tempting-wrong) probe. No task contains personal data.
- **Labels / ground truth.** The oracle proves single-command solvability
  (validated 56/56 under all four contracts); ground truth is the final program
  *state*, computed in Python from the payload, not a reference command string.
- **Splits.** The public artifact contains the frozen 56-task diagnostic core.
  A preregistered confirmation uses 42 private hostile variants sampled from the
  same 14 operation families; those tasks test payload generalization rather than
  new operation categories.
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

- **Intended.** Measuring one-shot Bash quoting reliability, fixed-reply transport damage,
  contract-conditioned compensation, and executable outcomes under different
  command interfaces and execution toolchains.
- **Out of scope / caution.** Not a general shell-competence or end-to-end agent
  benchmark; not an estimate of real-world failure prevalence; not evidence that
  one typed interface is universally superior. Discovery cells are mostly
  single-generation, while reliability claims rely on the three-trial Study B and
  the separately preregistered private confirmation. The 14 operation families,
  not the 56 correlated task instances or thousands of attempts, are the primary
  inferential units.
- **Safety.** Some tasks use hostile filenames (leading dash, glob-lookalikes)
  and destructive-looking operations; evaluate untrusted (e.g. open-weight)
  model output under `--executor docker` (`--network none`, isolated tempdir).


## Coverage and provenance (v1.0 additions)

- **Frozen diagnostic core.** The 56 tasks are a frozen, human-audited,
  incident-derived diagnostic core — not a sampled corpus and not a
  leaderboard-scale test set. A pre-release coverage survey (86 internal
  incidents; 412 public candidates screened; 34 read in full; 17 model-level
  POSIX quoting incidents extracted) mapped every surveyed incident to
  mechanisms already exercised by the core, and stopped when no new quoting
  primitive emerged. Generated stress splits are a planned extension and are
  versioned separately.
- **Three evidence layers.** Study A reports 15 configurations in the scorecard;
  eight have same-window raw/nested crossover evidence and seven extend matched
  nested coverage descriptively. The preregistered private crossover covers
  GPT-5.6-sol and Opus-4.8 on 42 tasks (168 accepted generations). A zero-call
  temporary-script bypass additionally replays accepted raw replies from those two
  models and Qwen3.5-27B, for 126 model--task pairs. Study B compares raw with native
  structured command tools on six models with three trials per cell and measured
  effort ladders: 8,736 generation records, 4,368 paired raw/native generations per
  userland, and 17,472 execution outcomes. Claims are scoped to the evidence layer
  that supports them.
- **Interface provenance (Study B).** Each model is driven through its
  provider API for structured tool calling; the model emits a structured shell
  tool call and the benchmark extracts the `command` argument and runs it in an
  isolated `bash -c` runner, never in the provider's environment. These are
  provider tool APIs, not a single unified wire protocol; what is held constant
  is the extracted `command`-string boundary and the downstream execution.
- **Accepted vs exploratory runs.** The released analyses use only the six
  accepted model directories under `results/native-final/`. Sibling
  directories prefixed with `_` are invalid or exploratory runs (e.g. an
  earlier MCP-based elicitation path with a measurement-validity defect,
  a contaminated asymmetric-isolation run, and a concurrent-writer partial);
  they are retained for provenance and excluded from every reported number.
- **Known data caveats.** (1) 7 of 1,680 Fable-5 records (0.4%) lack token
  usage metadata; commands and outcomes are valid and retained, and the
  records are excluded from token summaries only. (2) The Opus-4.8 `max`
  effort block was appended after the four lower rungs completed: max
  raw/native interleave with each other but not with the earlier window.
  (3) In BSD records a non-adherent native attempt stores `command: null`;
  the GNU replay of the same attempt serializes it as an empty string — the
  20 such records are exactly the non-adherent attempts.
- **Training/evaluation separation.** The frozen public core is intended for audit,
  regression, and mechanism diagnosis. Training releases should use separately
  generated variants. Future public comparisons should version hidden or newly
  generated payloads and disclose whether the frozen core was used for training;
  one-task differences among adjacent frontier configurations should not be treated
  as stable ranks.
- **Mitigation evidence.** A zero-call three-model replay shows that a temporary
  script bypasses the hostile nested interpolation layer and preserves every raw-path
  outcome, but it does not repair commands that already fail on the raw path. A
  separate typed-operation pilot changes both representation and executor and is not
  evidence that schemas are universally safer.
- **BSD/GNU replay semantics.** GNU numbers re-execute the stored,
  BSD-elicited commands verbatim on a pinned GNU/docker toolchain (no model
  re-calls). They measure how commands *transfer* across dialects, not which
  dialect a model would target if instructed differently.
- **Finite diagnostic scorecard, not a fine-grained leaderboard.** The 56 tasks form 14 correlated operation
  families. Primary Study-A and Study-B inference uses enumerated family-sign
  sensitivity tests under a stated symmetry null, Holm correction where
  predeclared, and leave-one-family-out ranges. These are finite-benchmark and
  model-based sensitivity statements, not population-sampling guarantees. Use
  the benchmark to identify large interface effects, not to rank adjacent models.

## Distribution & maintenance

- **Release.** Public code repository (frozen 56-task core + harness + programmatic API client +
  scorer + replay + Dockerfile), released under Apache-2.0 with NOTICE
  attribution and `CITATION.cff` metadata. Release and preprint packaging are
  performed manually after local scientific and visual review.
- **Versioning.** Core frozen at 56 tasks. Generated training variants, hidden or
  newly generated evaluation payloads, v0.3 scenarios, PowerShell, and long-context
  tracks are versioned separately. Reports must name the core/version, model snapshot,
  model identifier, evaluation configuration, effort setting, and whether the public core influenced training.
- **Contact / contributions.** Via the repository.
