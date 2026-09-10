"""Strict typed-operation transport used by the preregistered mitigation pilot.

The transport accepts a small JSON program and executes it without a command-string
shell. It deliberately supports only the operation families included in the pilot.
All paths remain inside the fresh task fixture, and argv execution is allowlisted.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

from .core import CheckResult, ExecResult, Task, snapshot
from .harness import Attempt, DOCKER_IMAGE, TIMEOUT_S, classify

MAX_OPERATIONS = 4
MAX_TEXT_BYTES = 65536
_ALLOWED_EXECUTABLES = {"./record", "./check_env", "git"}
_ALLOWED_GIT_SUBCOMMANDS = {"add", "commit"}
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class TypedProgramError(ValueError):
    pass


def _strict_keys(value: Mapping[str, Any], allowed: set[str], context: str) -> None:
    extra = set(value) - allowed
    if extra:
        raise TypedProgramError(f"{context}: unsupported key(s): {sorted(extra)}")


def _relative_path(root: Path, raw: Any, context: str) -> Path:
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise TypedProgramError(f"{context}: path must be a nonempty string")
    candidate = Path(raw)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise TypedProgramError(f"{context}: path must stay inside the fixture")
    resolved = (root / candidate).resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise TypedProgramError(f"{context}: path escapes the fixture")
    return resolved


def parse_typed_program(text: str) -> list[dict[str, Any]]:
    if not isinstance(text, str) or not text.strip():
        raise TypedProgramError("response is empty")
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise TypedProgramError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise TypedProgramError("top level must be an object")
    _strict_keys(value, {"operations"}, "top level")
    operations = value.get("operations")
    if not isinstance(operations, list) or not (1 <= len(operations) <= MAX_OPERATIONS):
        raise TypedProgramError(f"operations must contain 1..{MAX_OPERATIONS} entries")
    normalized: list[dict[str, Any]] = []
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise TypedProgramError(f"operation {index}: expected object")
        op = operation.get("op")
        if op == "write_file":
            _strict_keys(operation, {"op", "path", "content"}, f"operation {index}")
            if not isinstance(operation.get("content"), str):
                raise TypedProgramError(f"operation {index}: content must be a string")
            if len(operation["content"].encode("utf-8")) > MAX_TEXT_BYTES:
                raise TypedProgramError(f"operation {index}: content is too large")
        elif op == "rename":
            _strict_keys(operation, {"op", "src", "dst"}, f"operation {index}")
        elif op == "remove":
            _strict_keys(operation, {"op", "path"}, f"operation {index}")
        elif op == "run":
            _strict_keys(operation, {"op", "argv", "env", "stdin"}, f"operation {index}")
            argv = operation.get("argv")
            if not isinstance(argv, list) or not argv or not all(
                isinstance(item, str) and "\x00" not in item for item in argv
            ):
                raise TypedProgramError(f"operation {index}: argv must be nonempty strings")
            if argv[0] not in _ALLOWED_EXECUTABLES:
                raise TypedProgramError(f"operation {index}: executable {argv[0]!r} is not allowed")
            if argv[0] == "git" and (len(argv) < 2 or argv[1] not in _ALLOWED_GIT_SUBCOMMANDS):
                raise TypedProgramError(f"operation {index}: unsupported git subcommand")
            env = operation.get("env", {})
            if not isinstance(env, dict) or len(env) > 8:
                raise TypedProgramError(f"operation {index}: env must be a small object")
            for key, val in env.items():
                if not isinstance(key, str) or not _ENV_NAME.fullmatch(key):
                    raise TypedProgramError(f"operation {index}: invalid environment name")
                if key in {"PATH", "HOME", "LD_PRELOAD", "BASH_ENV", "ENV"}:
                    raise TypedProgramError(f"operation {index}: protected environment name")
                if not isinstance(val, str) or "\x00" in val:
                    raise TypedProgramError(f"operation {index}: environment values must be strings")
            stdin = operation.get("stdin", "")
            if not isinstance(stdin, str) or len(stdin.encode("utf-8")) > MAX_TEXT_BYTES:
                raise TypedProgramError(f"operation {index}: stdin must be a bounded string")
        else:
            raise TypedProgramError(f"operation {index}: unsupported op {op!r}")
        normalized.append(dict(operation))
    return normalized


def _docker_argv(
    argv: list[str], cwd: Path, env: Mapping[str, str], stdin: str, timeout: int
) -> ExecResult:
    command = [
        "docker", "run", "--rm", "--network", "none", "-i",
        "-v", f"{cwd}:/work", "-w", "/work",
        "-e", "HOME=/work", "-e", "LC_ALL=C", "-e", "LANG=C",
        "-e", "TERM=dumb", "-e", "GIT_CONFIG_NOSYSTEM=1",
        "-e", "GIT_CONFIG_COUNT=1", "-e", "GIT_CONFIG_KEY_0=safe.directory",
        "-e", "GIT_CONFIG_VALUE_0=/work",
    ]
    for key, value in sorted(env.items()):
        command.extend(["-e", f"{key}={value}"])
    command.extend([DOCKER_IMAGE, *argv])
    started = time.monotonic()
    try:
        result = subprocess.run(
            command, input=stdin, capture_output=True, text=True,
            errors="replace", timeout=timeout + 30,
        )
        return ExecResult(
            result.returncode, result.stdout, result.stderr,
            duration=time.monotonic() - started,
        )
    except subprocess.TimeoutExpired as exc:
        return ExecResult(
            -1, exc.stdout or "", exc.stderr or "", timed_out=True,
            duration=time.monotonic() - started,
        )


def _validate_attempt(task: Task, root: Path, pre: set[str], res: ExecResult, label: str) -> Attempt:
    check = task.check(root, res)
    post = snapshot(root)
    new_files = sorted(post - pre)
    if check.ok and task.allowed_new_files is not None:
        unexpected = sorted(set(new_files) - set(task.allowed_new_files))
        if unexpected:
            check = CheckResult(False, "unexpected extra file(s): " + ", ".join(unexpected))
    return Attempt(
        command=label, exit_code=res.exit_code, stdout=res.stdout[-2000:],
        stderr=res.stderr[-2000:], timed_out=res.timed_out, passed=check.ok,
        reason=check.reason, error_class=classify(res, check), new_files=new_files,
    )


def run_typed_attempt(task: Task, program_text: str, timeout: int = TIMEOUT_S) -> Attempt:
    root = Path(tempfile.mkdtemp(prefix="qb-typed-"))
    try:
        task.setup(root)
        pre = snapshot(root)
        try:
            operations = parse_typed_program(program_text)
        except TypedProgramError as exc:
            return Attempt(
                command=program_text, exit_code=-1, stdout="", stderr="",
                timed_out=False, passed=False, reason=str(exc),
                error_class="typed-schema", new_files=[],
            )
        stdout: list[str] = []
        stderr: list[str] = []
        last = ExecResult(0, "", "")
        for index, operation in enumerate(operations):
            try:
                if operation["op"] == "write_file":
                    path = _relative_path(root, operation["path"], f"operation {index}")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(operation["content"])
                    last = ExecResult(0, "", "")
                elif operation["op"] == "rename":
                    src = _relative_path(root, operation["src"], f"operation {index} src")
                    dst = _relative_path(root, operation["dst"], f"operation {index} dst")
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    src.rename(dst)
                    last = ExecResult(0, "", "")
                elif operation["op"] == "remove":
                    path = _relative_path(root, operation["path"], f"operation {index}")
                    if path.is_dir():
                        raise TypedProgramError("remove only accepts files")
                    path.unlink()
                    last = ExecResult(0, "", "")
                else:
                    last = _docker_argv(
                        operation["argv"], root, operation.get("env", {}),
                        operation.get("stdin", ""), timeout,
                    )
                    stdout.append(last.stdout)
                    stderr.append(last.stderr)
                    if last.timed_out or last.exit_code != 0:
                        break
            except (OSError, TypedProgramError) as exc:
                return Attempt(
                    command=program_text, exit_code=1, stdout="", stderr=str(exc),
                    timed_out=False, passed=False, reason=str(exc),
                    error_class="typed-execution", new_files=[],
                )
        combined = ExecResult(
            last.exit_code, "".join(stdout), "".join(stderr), last.timed_out,
            duration=last.duration,
        )
        return _validate_attempt(task, root, pre, combined, program_text)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def run_script_attempt(task: Task, script: str, timeout: int = TIMEOUT_S) -> Attempt:
    """Replay a stored command as a temporary Bash script in the pinned container."""
    root = Path(tempfile.mkdtemp(prefix="qb-script-"))
    script_path = root / ".qb-transport-script.sh"
    try:
        task.setup(root)
        pre = snapshot(root)
        script_path.write_text(script)
        argv = [
            "docker", "run", "--rm", "--network", "none",
            "-v", f"{root}:/work", "-w", "/work",
            "-e", "HOME=/work", "-e", "LC_ALL=C", "-e", "LANG=C",
            "-e", "TERM=dumb", "-e", "GIT_CONFIG_NOSYSTEM=1",
            "-e", "GIT_CONFIG_COUNT=1", "-e", "GIT_CONFIG_KEY_0=safe.directory",
            "-e", "GIT_CONFIG_VALUE_0=/work",
            DOCKER_IMAGE, "bash", "/work/.qb-transport-script.sh",
        ]
        started = time.monotonic()
        try:
            result = subprocess.run(
                argv, capture_output=True, text=True, errors="replace",
                timeout=timeout + 30,
            )
            res = ExecResult(
                result.returncode, result.stdout, result.stderr,
                duration=time.monotonic() - started,
            )
        except subprocess.TimeoutExpired:
            res = ExecResult(-1, "", "", timed_out=True, duration=time.monotonic() - started)
        script_path.unlink(missing_ok=True)
        return _validate_attempt(task, root, pre, res, "<temporary-script>")
    finally:
        shutil.rmtree(root, ignore_errors=True)
