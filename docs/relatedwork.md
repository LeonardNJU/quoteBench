# Where QuoteBench sits

Existing terminal and command-generation benchmarks score the generated program
once. QuoteBench holds that program fixed, changes the execution transport, and
attributes each loss to transport or to generation.

## What neighbouring benchmarks measure

Each row follows the benchmark's own description. *Interface varied*: the same
task is evaluated under more than one execution interface; *regenerated* means
the model produces a fresh output per interface. *Replay*: identical model output
is re-executed under a changed interface. *Quoting*: quoting and escaping hazards
are the stated target.

| Work | Unit scored | Correctness signal | Interface varied | Replay | Quoting |
|---|---|---|---|---|---|
| NL2Bash | NL to one-line Bash, 9,305 pairs | structure and full-command accuracy against references | no | no | no |
| NLC2CMD | NL to Bash | utility and flag match, arguments ignored | no | no | no |
| InterCode-Bash | interactive Bash, 200 tasks | execution: output similarity, file-system diff, file hashes against a gold command | no | no | no |
| NL2SH-ALFA | NL to Bash, 600 test pairs | execution plus LLM-judged functional equivalence | no | no | no |
| BashBench | Bash commands and scripts, 952 | execution test suite, `bash -n`, ShellCheck | no | no | no |
| Terminal-Bench | agent terminal tasks | per-task test script | no | no | no |
| TerminalWorld | agent terminal tasks, 1,530 | test assertions on final state | no | no | no |
| EnvBench | environment setup, 994 repositories | static import or compile check | no | no | no |
| SWE-agent | SWE-bench issues | hidden unit tests | ACI versus shell, regenerated | no | no |
| OctoBench | repository-grounded coding, 217 tasks | objective checklist items | three scaffolds, regenerated | no | no |
| **QuoteBench** | one-shot Bash, 56 tasks | exact final state, audited validators | contract × transport, crossed; real ssh | **yes** | **yes** |

## Closest in prescription

Concurrent work shows that swapping an agent's whole harness reorders
leaderboards and asks evaluators to disclose the harness. Because it replaces
context handling, retry, and verification together, it measures variance and
cannot attribute a reversal to a mechanism. QuoteBench fixes the model output and
changes a single parser, so the matched score decomposes into transport damage
and contract-conditioned compensation. Prompt-format sensitivity work
establishes that scores move with a nuisance variable, but each format is
regenerated, so the spread cannot be separated into what the channel destroyed
and what the model produced differently.

## Why the failure mode is real

- Public issue trackers for coding agents repeatedly document heredoc,
  apostrophe, nested-shell, and cross-shell wrapping failures.
- Agent frameworks often steer users away from shell editing paths and toward
  structured editor tools, which removes the skill from the score.
- QuoteBench removes common escape hatches so quoting itself becomes visible in
  the score.

## Design sources

Task hazards are grounded in ShellCheck rule families, Wheeler's hostile
filename taxonomy, and real shell edge cases such as sed replacement escaping,
heredoc delimiter collision, and nested ssh-style evaluation. The paper's
Related Work section carries the citations.
