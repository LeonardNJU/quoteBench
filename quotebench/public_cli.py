"""Public QuoteBench command line for offline validation and scoring."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .harness import run_attempt
from .rollouts import summarize_rollouts, verify_rollouts
from .scenarios import all_tasks, get_task


def command_for_transport(contract: str, reply: str) -> str:
    if contract in {"raw", "native"}:
        return reply
    if contract == "nested-shell":
        return 'bash -c "' + reply + '"'
    raise ValueError(contract)


def cmd_score(args: argparse.Namespace) -> int:
    records = [json.loads(line) for line in Path(args.input).read_text().splitlines() if line.strip()]
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
