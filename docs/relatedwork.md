# Related work (living doc)

Survey date: 2026-07-11 (three parallel deep-research passes; raw notes
summarized here). **Conclusion: no existing benchmark isolates shell
quoting/escaping as the measured skill; the niche is empty.**

## Direct competitors

None found — across web/GitHub/HuggingFace/arXiv searches and a clone+grep of
the Terminal-Bench task registry (241 tasks, zero quoting-focused; its error
taxonomy has no quoting category). The name "QuoteBench" collides only with an
unrelated 3D-printing app; "EscapeBench/escape-bench" names are taken by
sandbox-escape safety evals (avoid that word in a paper title).

## Near misses (and what we take from each)

| work | what it is | relation |
|---|---|---|
| [Terminal-Bench / TB2.0](https://github.com/laude-institute/terminal-bench) (arXiv:2601.11868) | 89-task agentic terminal bench, state-based verification, Harbor harness | quoting exercised incidentally, never isolated. Take: state-based verification; Harbor packaging as distribution channel; the "TB subsumes this" reviewer objection is rebutted by the grep evidence above |
| [NL2SH-ALFA](https://arxiv.org/abs/2502.06858) (NAACL 2025) | 600-pair NL→one bash command, execution-based functional-equivalence judging | closest methodology (one-shot command synthesis + execution verification), benign strings only. Take: their audit found >50% errors in InterCode's gold commands → machine-construct + execution-verify our oracles |
| [InterCode-Bash](https://arxiv.org/pdf/2306.14898) (NeurIPS 2023) | interactive bash env, filesystem-diff reward | verification pattern; gold-data cautionary tale |
| [NLC2CMD](https://arxiv.org/abs/2103.02523) / [NL2Bash](https://arxiv.org/abs/1802.08979) | NL→bash template-match evals | negative precedent: template metrics erase arguments, making quoting literally invisible to the score |
| [tau-bench](https://arxiv.org/abs/2406.12045) | tool-use bench, goal-state verification | pass^k reliability metric — right shape for tail-event failures like quoting |
| [BashBench/Ctrl-Z](https://arxiv.org/abs/2504.10374), [AgentBench-OS](https://arxiv.org/abs/2308.03688), [EnvBench](https://arxiv.org/abs/2503.14443), [TerminalWorld](https://arxiv.org/abs/2605.22535), [carlini/yet-another](https://github.com/carlini/yet-another-applied-llm-benchmark) | other shell/terminal benches | none isolate quoting (checked task sources where public) |
| [ShIOEnv](https://arxiv.org/pdf/2505.18374) | grammar-constrained bash argument construction env | adjacent: argument composition as RL env, not a quoting skill probe |

## Evidence the failure mode is real (motivation section material)

- claude-code issues: [#48317](https://github.com/anthropics/claude-code/issues/48317) (heredoc-over-ssh retry loop), [#29619](https://github.com/anthropics/claude-code/issues/29619) (apostrophe kills `gh pr create`, 4+ times/session, proposes temp-file workaround), [#7387](https://github.com/anthropics/claude-code/issues/7387)/[#1132](https://github.com/anthropics/claude-code/issues/1132)/[#11225](https://github.com/anthropics/claude-code/issues/11225) (the harness's OWN escaping layer corrupts commands — both model and harness fail at this), [#16163](https://github.com/anthropics/claude-code/issues/16163) ($ARGUMENTS injection), Windows-apostrophe-in-path family [#28759](https://github.com/anthropics/claude-code/issues/28759)
- codex: [#20875](https://github.com/openai/codex/issues/20875) (models over-quote because they can't tell argv-shaped from script-shaped strings — tool-contract ambiguity), [#11360](https://github.com/openai/codex/issues/11360) (asks for pre-execution syntax checks — still unmet), [#7298](https://github.com/openai/codex/issues/7298) (cross-shell wrapping)
- gemini-cli [#1839](https://github.com/google-gemini/gemini-cli/issues/1839), warp [#7735](https://github.com/warpdotdev/warp/issues/7735), OpenHands [#1934](https://github.com/OpenHands/OpenHands/issues/1934)
- ["Terminal Agents Suffice for Enterprise Automation"](https://arxiv.org/html/2604.00073v2): documents an agent falling back to writing the payload to a file after two quoting failures — the exact escape hatch this bench removes
- [SWE-agent](https://arxiv.org/pdf/2405.15793) (NeurIPS 2024): shell-only agents −7.7 pts vs ACI editor; "sed makes multi-line edits cumbersome... hard to detect" — the canonical academic statement
- arXiv:2512.07497: escaping named as a non-recoverable failure pattern ("wrong escaping for newline chars... never recovers")

## Mitigations in the wild (what agents are steered to instead)

Claude Code system prompt: "use Edit not sed/awk, Write not echo/heredoc" + canned
heredoc idiom for git commits; codex: argv-array exec + grammar-constrained
apply_patch (removes one quoting layer entirely); aider: no shell edit path at
all; OpenHands: str_replace_editor moved out of bash. I.e., the entire industry
mitigates by *avoiding* the skill — none measure or train it. That is the gap.

## Design sources

- [Wheeler, "Fixing Unix/Linux/POSIX Filenames"](https://dwheeler.com/essays/fixing-unix-linux-filenames.html) + [filenames-in-shell](https://dwheeler.com/essays/filenames-in-shell.html) — hostile-filename taxonomy (tiers of hostile-filenames/bulk-rename scenarios)
- git test suite `t3300-funny-names.sh`, `t3902-quoted.sh`, `t4016-diff-quote.sh` — fixture recipes
- ShellCheck wiki — hazard tags: SC2086 (word splitting), SC2016 (wrong quote type), SC2029/2087/2089/2090 (cross-boundary), SC2046/2061/2068 — used as `hazards` labels on tasks
