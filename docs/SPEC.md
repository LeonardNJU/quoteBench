# QuoteBench public specification

## Scope

QuoteBench is a finite, execution-verified diagnostic of literal preservation in
one-shot Bash generation. The frozen core contains 14 operation families and four
payload variants per family: one benign control and three hazardous variants. The
hazardous variants probe distinct mechanisms rather than a monotone difficulty scale.

## Model-facing conventions

- `raw`: the model emits one Bash program.
- `native`: the model emits a structured shell-tool call; QuoteBench extracts the
  required command string and uses the same downstream executor.

## Controlled transport

- `nested`: the returned text is interpolated, deliberately without escaping, into
  an additional double-quoted `bash -c "R"` boundary.

Nested is a synthetic stressor, not a third surveyed model-facing convention, a
universal production path, or a prevalence estimate.

## Crossover estimands

For generation contract `G` and execution transport `T`, `Y_GT` is the final-state
outcome of the stored reply:

```text
fixed-reply transport damage = RN - RR
realized compensation        = NN - RN
matched gap                  = NN - RR
```

`RN - RR` fixes the reply and locates the net success difference after generation.
`NN - RN` compares separately generated replies and describes the realized
contract-conditioned contrast in this finite benchmark. Low `RN` with high `NN`
diagnoses cross-protocol sensitivity, while the matched `NN` score describes the
declared nested path.

## Correctness

Every task builds its fixture in Python and checks exact final program state. The
validator does not require a reference command string. Oracle, untouched-state,
naive-probe, collateral-file, timeout, and mutation gates are part of the release.

## Evidence and reporting

Study A's broad matched-nested profile contains 15 frozen configurations. All causal
transport-damage and compensation claims use the eight configurations collected in one
same-window crossover; the seven additional rows extend matched-nested descriptive
coverage only and are not used to reconstruct off-diagonal cells. The headline
scorecard selects the best observed measured setting for each of 13 base models;
providers expose different numbers of settings, so this summary is descriptive.
Study B contains 8,736 generations and 17,472 BSD/GNU execution outcomes across six
models and is exploratory. Effort claims apply only to the measured provider-exposed
operating points.

Every reported result should identify the task version, public model identifier,
generation contract, execution transport, userland, effort setting, serving date,
and whether the cell is same-window, single-draw, or multi-trial.
