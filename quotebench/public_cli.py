"""Public QuoteBench command line: validate, run a model, score, and crossover."""
from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .core import CONTRACT_PROMPTS
from .crossover import (
    TARGET_CONTRACTS, command_for, generated_reply, paired_keys, summarize_crossover,
)
from .harness import run_attempt
from .rollouts import summarize_rollouts, verify_rollouts
from .runner import add_client_arguments, client_from_args
from .scenarios import all_tasks, get_task

RUN_CONTRACTS = ("raw", "nested-shell-v2")


def command_for_transport(contract: str, reply: str) -> str:
    """Command executed for a reply generated under `contract`.

    `native` replies are the tool argument itself. Text contracts map to the
    raw or nested transport through the crossover helper, which also accepts
    the legacy `wrapped` spelling.
    """
    if contract == "native":
        return reply
    return command_for(reply, contract)


def _read_jsonl(path: str) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def cmd_score(args: argparse.Namespace) -> int:
    records = _read_jsonl(args.input)
    passed = 0
    with Path(args.out).open("w") as handle:
        for record in records:
            if record.get("schema_version") == "quotebench-rollout-v1":
                task_id = record["task"]["task_id"]
                contract = record["sampling"]["contract"]
                reply = record["response"]["reply"]
            else:
                task_id = record["task_id"]
                contract = record["contract"]
                reply = record["reply"]
            task = get_task(str(task_id))
            command = command_for_transport(str(contract), str(reply))
            attempt = run_attempt(task, command, executor=args.executor)
            output = {**record, "passed": attempt.passed, "reason": attempt.reason,
                      "error_class": attempt.error_class, "exit_code": attempt.exit_code}
            handle.write(json.dumps(output, ensure_ascii=False) + "\n")
            passed += int(attempt.passed)
    print(f"passed {passed}/{len(records)}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    failures = []
    for task in all_tasks():
        attempt = run_attempt(task, task.oracle, executor=args.executor)
        if not attempt.passed:
            failures.append((task.task_id, attempt.reason))
    if failures:
        for task_id, reason in failures:
            print(f"FAIL {task_id}: {reason}")
        return 1
    print("oracle validation passed for all 56 tasks")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """One generation per task per trial, executed on the contract's transport.

    The system prompt is the contract prompt and the user message is the task
    instruction. There is no repair feedback loop. Each record is appended to
    the output file as soon as it completes; --resume skips records on disk.
    """
    client = client_from_args(args)
    system_prompt = CONTRACT_PROMPTS[args.contract]
    tasks = all_tasks()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if args.resume and out.exists():
        done = {(r["task_id"], r["trial"]) for r in _read_jsonl(str(out))}
    elif out.exists():
        out.unlink()
    work = [(trial, task) for trial in range(args.trials) for task in tasks
            if (task.task_id, trial) not in done]
    print(f"{len(done)} records on disk, {len(work)} to run")

    def run_one(trial, task) -> dict:
        generation = client.complete(system_prompt, task.instruction)
        command = command_for_transport(args.contract, generation.text)
        attempt = run_attempt(task, command, executor=args.executor)
        return {
            "task_id": task.task_id, "scenario": task.scenario, "tier": task.tier,
            "hazards": task.hazards, "contract": args.contract, "model": args.model,
            "trial": trial,
            "reply": generation.text, "raw_reply": generation.raw_text,
            "reply_cleanup": generation.cleanup, "command": command,
            "passed": attempt.passed, "reason": attempt.reason,
            "error_class": attempt.error_class, "exit_code": attempt.exit_code,
            "stderr": attempt.stderr,
            "usage": generation.usage, "latency": generation.latency,
            "finish_reason": generation.finish_reason,
        }

    passed = 0
    with out.open("a") as handle, ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(run_one, trial, task) for trial, task in work]
        try:
            for future in as_completed(futures):
                record = future.result()
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                handle.flush()
                passed += int(record["passed"])
                status = "PASS" if record["passed"] else f"FAIL {record['error_class']}"
                print(f"[{record['trial']}] {record['task_id']:40s} {status}")
        except BaseException:
            pool.shutdown(cancel_futures=True)
            raise
    print(f"passed {passed}/{len(work)} new records -> {out}")
    return 0


def cmd_crossover(args: argparse.Namespace) -> int:
    """Replay every stored reply through both transports and print the 2x2 table."""
    def load(path: str) -> list[dict]:
        records = _read_jsonl(path)
        if args.all_trials:
            return records
        return [r for r in records if int(r.get("trial", 0)) == 0]

    raw, nested, keys = paired_keys(load(args.raw), load(args.disclosed))
    rows = []
    for key in keys:
        task = get_task(key.task_id)
        for source, records in (("raw", raw), ("nested-shell", nested)):
            reply = generated_reply(records[key], source)
            for target in TARGET_CONTRACTS:
                attempt = run_attempt(task, command_for(reply, target), executor=args.executor)
                rows.append({
                    "task_id": key.task_id, "trial": key.trial,
                    "source_contract": source, "target_contract": target,
                    "passed": attempt.passed, "reason": attempt.reason,
                    "error_class": attempt.error_class,
                })
    summary = summarize_crossover(rows)
    rates = summary["rates_pct"]
    cells = {
        "RR": rates["raw_generated__raw_transport"],
        "RN": rates["raw_generated__nested-shell_transport"],
        "NR": rates["nested-shell_generated__raw_transport"],
        "NN": rates["nested-shell_generated__nested-shell_transport"],
    }
    effects = {
        "damage_pp": cells["RN"] - cells["RR"],
        "compensation_pp": cells["NN"] - cells["RN"],
        "matched_gap_pp": cells["NN"] - cells["RR"],
    }
    trials = sorted({key.trial for key in keys})
    for name, value in cells.items():
        print(f"{name} {value:6.1f}%")
    print(f"damage (RN-RR)       {effects['damage_pp']:+6.1f} pp")
    print(f"compensation (NN-RN) {effects['compensation_pp']:+6.1f} pp")
    print(f"matched gap (NN-RR)  {effects['matched_gap_pp']:+6.1f} pp")
    print(f"cells over {len(keys)} (task, trial) pairs, trials {trials}")
    Path(args.out).write_text(json.dumps({
        "cells_pct": cells, "effects_pp": effects,
        "n_pairs": len(keys), "trials": trials,
        "crossover": summary, "rows": rows,
    }, indent=2) + "\n")
    print(f"wrote crossover summary -> {args.out}")
    return 0


def cmd_verify_rollouts(args: argparse.Namespace) -> int:
    result = verify_rollouts(Path(args.root))
    print(
        f"rollout verification passed: {result['rollouts']} records, "
        f"{result['arm_files']} arm files, {result['files']} release files"
    )
    return 0


def cmd_summarize_rollouts(args: argparse.Namespace) -> int:
    result = summarize_rollouts(Path(args.root))
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote public rollout analysis -> {args.out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quotebench")
    sub = parser.add_subparsers(dest="command", required=True)
    score = sub.add_parser("score")
    score.add_argument("--input", required=True)
    score.add_argument("--out", required=True)
    score.add_argument("--executor", choices=("local", "docker"), default="docker")
    score.set_defaults(func=cmd_score)
    validate = sub.add_parser("validate")
    validate.add_argument("--executor", choices=("local", "docker"), default="docker")
    validate.set_defaults(func=cmd_validate)
    run = sub.add_parser("run")
    run.add_argument("--contract", choices=RUN_CONTRACTS, required=True)
    run.add_argument("--out", required=True)
    run.add_argument("--executor", choices=("local", "docker"), default="docker")
    run.add_argument("--trials", type=int, default=1)
    run.add_argument("--concurrency", type=int, default=4)
    run.add_argument("--resume", action="store_true",
                     help="keep records already in --out and run only the missing ones")
    add_client_arguments(run)
    run.set_defaults(func=cmd_run)
    crossover = sub.add_parser("crossover")
    crossover.add_argument("--raw", required=True, help="run output with --contract raw")
    crossover.add_argument("--disclosed", required=True,
                           help="run output with --contract nested-shell-v2")
    crossover.add_argument("--out", required=True)
    crossover.add_argument("--executor", choices=("local", "docker"), default="docker")
    crossover.add_argument("--all-trials", action="store_true",
                           help="pool every trial instead of trial 0 only")
    crossover.set_defaults(func=cmd_crossover)
    verify = sub.add_parser("verify-rollouts")
    verify.add_argument("--root", required=True)
    verify.set_defaults(func=cmd_verify_rollouts)
    summarize = sub.add_parser("summarize-rollouts")
    summarize.add_argument("--root", required=True)
    summarize.add_argument("--out", required=True)
    summarize.set_defaults(func=cmd_summarize_rollouts)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return int(args.func(args))
