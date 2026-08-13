# Reproducing QuoteBench evaluations

## 1. Build the pinned execution environment

```bash
docker build -t quotebench-runner .
```

## 2. Validate all task oracles

```bash
python3 -m quotebench validate --executor docker
```

This must report oracle success for all 56 frozen tasks.

## 3. Score stored generations

The scorer accepts either one JSON object per line with `task_id`, `contract`, and
`reply`, or records in the released `quotebench-rollout-v1` schema:

```bash
python3 -m quotebench score \
  --input generations.jsonl \
  --out scored.jsonl \
  --executor docker
```

Evaluators may produce this JSONL format with any model-access system they control.
Fresh acquisition is not required to replay or validate the paper's frozen evidence.

## 4. Verify released rollout records

The public dataset uses `quotebench-rollout-v1`, where one generation is one rollout
and re-executions are entries in `executions`. After downloading the dataset:

```bash
python3 -m quotebench verify-rollouts --root QuoteBench-Rollouts
python3 -m quotebench summarize-rollouts \
  --root QuoteBench-Rollouts --out public-analysis.json
```

The first command fails closed on extra or missing files, checks every SHA-256,
validates all records against `rollout-schema.json`, and reconciles file and record
counts with `MANIFEST.json`. The second emits every observed campaign/model/contract/
effort/trial/toolchain rate plus the public GNU crossover table and ranking-reversal
count.

The public rollout dataset contains the auditable stored-reply campaigns used for the
released analyses, together with a schema, manifest, and SHA-256 checksums. Private
payloads are intentionally withheld to preserve the held-out split.
