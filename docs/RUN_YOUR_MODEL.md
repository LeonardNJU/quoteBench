# Run QuoteBench on your own model

This guide produces the paper's headline crossover numbers for a model you can
reach through an OpenAI-compatible chat completions API. You need the API base
URL, an API key, and a model identifier.

## Prerequisites

- Python 3.11 or newer. The package has no third-party dependencies.
- Docker, recommended. Model output is untrusted; the Docker executor runs it
  with networking disabled inside a pinned GNU image. Build the image once:

```bash
docker build -t quotebench-runner .
python3 -m quotebench validate --executor docker
```

Without Docker, pass `--executor local` to every command below. Local
execution runs model output in a temporary directory on your machine with your
own userland. Report which executor you used.

## Configuration

```bash
export QUOTEBENCH_API_BASE=https://api.openai.com/v1   # default when unset
export QUOTEBENCH_API_KEY=sk-...
```

`--base-url` overrides `QUOTEBENCH_API_BASE`. `--api-key-env NAME` reads the key
from a different environment variable. `--model` can also come from
`QUOTEBENCH_MODEL`. `--extra-body` merges a JSON object into every request, for
example `--extra-body '{"reasoning_effort": "high"}'` or
`--extra-body '{"temperature": 0}'`. `--max-tokens` sets the `max_tokens`
field; models that require `max_completion_tokens` take it through
`--extra-body` instead.

Any server that accepts `POST {base-url}/chat/completions` with a bearer token
and returns `choices[0].message.content` works. HTTP 429 and 5xx responses are
retried with exponential backoff. Other errors stop the run; rerun with
`--resume` to continue from the records already written.

## The three commands

```bash
python3 -m quotebench run --contract raw \
  --model MODEL_ID --out results/MODEL_ID/raw.jsonl

python3 -m quotebench run --contract nested-shell-v2 \
  --model MODEL_ID --out results/MODEL_ID/disclosed.jsonl

python3 -m quotebench crossover \
  --raw results/MODEL_ID/raw.jsonl \
  --disclosed results/MODEL_ID/disclosed.jsonl \
  --out results/MODEL_ID/crossover.json
```

`run` sends one request per task. The system prompt is the contract prompt
and the user message is the task instruction. There is no repair loop. The
reply is stripped of outer whitespace, a `<think>` block, a Markdown fence, or
a leading `$ ` prompt, then executed on the contract's transport: verbatim for
`raw`, interpolated into `bash -c "R"` for `nested-shell-v2`. Both the verbatim
and the executed text are stored.

`crossover` takes the two run outputs and replays every stored reply through
both transports. It does not call the model.

Options: `--trials N` collects N generations per task; `--concurrency N` sets
parallel requests (default 4); `--resume` keeps the records already in `--out`.
`crossover` uses trial 0 unless `--all-trials` pools every trial.

## Expected outputs

Each `run` writes one JSON record per line with `task_id`, `scenario`, `tier`,
`hazards`, `contract`, `model`, `trial`, `reply`, `raw_reply`, `reply_cleanup`,
`command`, `passed`, `reason`, `error_class`, `exit_code`, `stderr`, `usage`
(prompt, completion, reasoning, and total tokens when the server reports them),
`latency` in seconds, and `finish_reason`. The file is accepted by
`python3 -m quotebench score` as is.

`crossover` prints four cells and three differences, and writes them to the
JSON file together with the per-task replay rows:

```text
RR   87.5%
RN   30.4%
NR   66.1%
NN   80.4%
damage (RN-RR)        -57.1 pp
compensation (NN-RN)  +50.0 pp
matched gap (NN-RR)    -7.1 pp
```

The numbers above are illustrative.

## Reading the cells

Each cell is the percentage of the 56 tasks whose final program state passed.
The first letter is the generation contract the reply was collected under, the
second is the transport it was executed on.

- `RR`: raw replies executed verbatim. The baseline.
- `RN`: the same raw replies interpolated into `bash -c "R"`. The reply is
  fixed, so `RN - RR` is transport damage after generation.
- `NR`: replies collected under the disclosed nested contract, executed
  verbatim.
- `NN`: disclosed replies executed on the nested transport. This is the
  matched path the model was told about.
- damage `RN - RR`: how much a fixed reply loses when an unescaped nested
  boundary is added.
- compensation `NN - RN`: how much the model recovers when the boundary is
  disclosed in the prompt.
- matched gap `NN - RR`: the difference between the two matched paths. A small
  matched gap with large damage means the aggregate score hides a command-path
  failure.

## Cost

One full crossover costs 56 tasks x 2 contracts x `--trials` requests, so 112
requests for a single trial. Prompts are short; usage is recorded per record so
the total can be summed from the output files. Execution adds no model calls.

## Validation

See `REPRODUCE.md` for oracle validation, scoring stored generations, and the
released rollout dataset. The `crossover.json` field `crossover` holds the
full decomposition in the same form as the released analyses.
