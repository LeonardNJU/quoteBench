"""QuoteBench CLI.

  python -m quotebench list                     # enumerate tasks
  python -m quotebench show <task_id>           # instruction + oracle + naive
  python -m quotebench export [--out PATH]      # JSONL manifest
  python -m quotebench validate [--executor X]  # oracle 100% + naive discrimination
  python -m quotebench run --adapter A [...]    # evaluate a model
  python -m quotebench score RESULTS.jsonl      # metrics report
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .adapters import make_adapter
from .core import CONTRACT_PROMPTS, SYSTEM_PROMPT
from .harness import Attempt, run_attempt
from .scenarios import all_tasks, get_task
from .context import wrap_instruction
from .shellesc import dq_embed_escape

try:  # v03 suite is optional (not shipped in the public core)
    from .scenarios_v03 import all_tasks_v03
except ImportError:
    def all_tasks_v03():
        return []
try:
    from .scenarios_l2 import all_tasks_l2
except ImportError:
    def all_tasks_l2():
        return []

HOSTILE = lambda t: t.tier > 0  # noqa: E731


def _suite_tasks(suite: str = "core"):
    if suite == "core":
        return all_tasks()
    if suite == "v03":
        return all_tasks_v03()
    if suite == "l2":
        return all_tasks_l2()
    if suite == "all":
        return all_tasks() + all_tasks_v03()
    raise SystemExit(f"unknown suite {suite!r} (core|v03|all)")


def _select(filter_str: str = "", suite: str = "core"):
    tasks = _suite_tasks(suite)
    if filter_str:
        tasks = [t for t in tasks if filter_str in t.task_id]
    return tasks


# ------------------------------------------------------------------ commands

def cmd_list(args) -> int:
    for t in _select(args.tasks, args.suite):
        print(f"{t.task_id:44s} tier={t.tier} hazards={','.join(t.hazards)}")
    print(f"-- {len(_select(args.tasks, args.suite))} tasks")
    return 0


def cmd_show(args) -> int:
    try:
        t = get_task(args.task_id)
    except KeyError:
        t = next((x for x in all_tasks_v03() if x.task_id == args.task_id), None)
        if t is None:
            raise
    print(f"# {t.task_id} (tier {t.tier})\nhazards: {', '.join(t.hazards)}\n")
    print("INSTRUCTION:\n" + t.instruction + "\n")
    print("ORACLE:\n" + t.oracle + "\n")
    print("NAIVE:\n" + (t.naive or "(none)"))
    return 0


def cmd_export(args) -> int:
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for t in _select(args.tasks, args.suite):
            f.write(json.dumps({
                "task_id": t.task_id, "scenario": t.scenario, "tier": t.tier,
                "hazards": t.hazards, "instruction": t.instruction,
                "oracle": t.oracle, "naive": t.naive,
            }, ensure_ascii=False) + "\n")
    print(f"wrote {len(_select(args.tasks, args.suite))} tasks -> {out}")
    return 0


def _oracle_reply(task, contract: str) -> str:
    """What a perfect model would REPLY under the given contract."""
    if contract == "raw":
        return task.oracle
    if contract == "json":
        return json.dumps({"command": task.oracle})
    if contract == "wrapped":
        return dq_embed_escape(task.oracle)
    raise ValueError(contract)


def _apply_contract(contract: str, reply: str):
    """Model reply text -> (command_to_execute, violation_msg)."""
    if contract == "raw":
        return reply, None
    if contract == "json":
        try:
            obj = json.loads(reply.strip())
            cmd = obj["command"]
            if not isinstance(cmd, str):
                raise ValueError('"command" is not a string')
            return cmd, None
        except (ValueError, KeyError, TypeError) as e:
            return None, f"JSON contract violation: {e}"
    if contract == "wrapped":
        return 'bash -c "' + reply + '"', None
    raise ValueError(contract)


def _violation_attempt(reply: str, msg: str) -> Attempt:
    return Attempt(command=reply, exit_code=-2, stdout="", stderr=msg,
                   timed_out=False, passed=False, reason=msg,
                   error_class="contract-error")


def cmd_validate(args) -> int:
    tasks = _select(args.tasks, args.suite)
    oracle_fail, weak, hard_controls = [], [], []
    for t in tasks:
        reply = _oracle_reply(t, args.contract)
        cmd, viol = _apply_contract(args.contract, reply)
        assert viol is None, f"oracle violates its own contract: {viol}"
        a = run_attempt(t, cmd, executor=args.executor)
        if not a.passed:
            oracle_fail.append((t, a))
            print(f"ORACLE FAIL {t.task_id}: {a.reason}")
            print(f"  cmd:    {t.oracle}")
            print(f"  exit={a.exit_code} stderr: {a.stderr.strip()[:300]}")
        if t.naive:
            # M3 fix: run discrimination under EVERY contract (transform the
            # naive reply the same way the model's reply would be), not raw only
            ncmd, nviol = _apply_contract(args.contract, t.naive)
            n = (_violation_attempt(t.naive, nviol) if nviol else
                 run_attempt(t, ncmd, executor=args.executor))
            if HOSTILE(t) and n.passed:
                weak.append(t.task_id)
            if not HOSTILE(t) and not n.passed:
                hard_controls.append((t.task_id, n.reason, n.stderr.strip()[:200]))
    n = len(tasks)
    print(f"\n[contract={args.contract}] oracle: {n - len(oracle_fail)}/{n} passed")
    print(f"discrimination: naive command passes {len(weak)} hostile task(s)"
          + (f" -> consider hardening: {weak}" if weak else " (good)"))
    if hard_controls:
        label = (
            "raw controls failed by naive (controls should be naive-passable):"
            if args.contract == "raw"
            else "controls failed by naive after transport contract "
                 "(reported, not a raw-discrimination gate):"
        )
        print(label)
        for tid, reason, err in hard_controls:
            print(f"  {tid}: {reason} | {err}")
    validation_failed = bool(
        oracle_fail or weak or (args.contract == "raw" and hard_controls)
    )
    return 1 if validation_failed else 0


_REPLY_FORMAT = {
    "raw": "Reply with a corrected single bash command (raw text, no fences, "
           "no explanation).",
    "json": 'Reply with a corrected JSON object {"command": "<bash command>"} '
            "(strict JSON, no fences, no explanation).",
    "wrapped": "Reply with a corrected command text R — as before, it will be "
               'interpolated into bash -c "R" (raw text, no fences, '
               "no explanation).",
}


def _feedback(a, contract: str = "raw") -> str:
    return (
        f"Your command exited with code {a.exit_code}"
        + (" (timed out)" if a.timed_out else "") + ".\n"
        f"stdout:\n{a.stdout or '(empty)'}\n"
        f"stderr:\n{a.stderr or '(empty)'}\n"
        f"The task was NOT accomplished: {a.reason or 'goal state not reached'}.\n"
        "The working directory has been RESET to its initial state. "
        + _REPLY_FORMAT[contract]
    )


def cmd_run(args) -> int:
    tasks = _select(args.tasks, args.suite)
    static = args.adapter in ("oracle", "naive")
    adapter = None if static else make_adapter(
        args.adapter, args.model, args.adapter_cmd, args.file,
        min_interval=args.min_interval,
        reasoning_effort=args.reasoning_effort)
    sys_prompt = CONTRACT_PROMPTS[args.contract]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    reqlog = out.with_suffix(".reqlog.jsonl")  # raw model I/O, append-only

    # checkpoint/resume: one JSONL record per (task, trial), written as soon as
    # it completes; --resume skips pairs already on disk
    records = []
    done = set()
    if args.resume and out.exists():
        with out.open() as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue  # partial trailing line from an interrupted run
                records.append(r)
                done.add((r["task_id"], r["trial"]))
    elif out.exists() and not args.resume:
        out.unlink()

    work = [(trial, t) for trial in range(args.trials) for t in tasks
            if (t.task_id, trial) not in done]
    if done:
        print(f"resume: {len(done)} records already on disk, {len(work)} to go")

    lock = threading.Lock()

    def gen(messages):
        # adapter hiccups (command non-zero exit, network) retry twice then raise
        for i in range(3):
            try:
                return adapter.generate(messages, sys_prompt)
            except Exception:
                if i == 2:
                    raise
                time.sleep(5 * (i + 1))

    def run_one(trial, t):
        attempts, raw_replies, metas = [], [], []
        if static or args.adapter == "file":
            reply = (adapter.lookup(t.task_id) if args.adapter == "file"
                     else _oracle_reply(t, args.contract) if args.adapter == "oracle"
                     else (t.naive or ""))
            cmd, viol = _apply_contract(args.contract, reply)
            raw_replies.append(reply); metas.append({})
            attempts.append(_violation_attempt(reply, viol) if viol else
                            run_attempt(t, cmd, executor=args.executor))
        else:
            messages = [{"role": "user", "content": wrap_instruction(t.instruction, args.context_tokens)}]
            for k in range(args.max_attempts):
                try:
                    g = gen(messages)
                except Exception as e:
                    raw_replies.append(""); metas.append({})
                    attempts.append(_violation_attempt(
                        "", f"adapter error: {e}"))
                    attempts[-1].error_class = "adapter-error"
                    break
                reply = g.text
                raw_reply = g.raw_text if g.raw_text != "" else reply
                raw_replies.append(raw_reply)
                meta = {"usage": g.usage, "latency": round(g.latency, 3)}
                if g.cleanup:
                    meta["reply_cleanup"] = g.cleanup
                    meta["cleaned_reply"] = reply
                metas.append(meta)
                with lock:
                    with reqlog.open("a") as lf:
                        lf.write(json.dumps(
                            {"task_id": t.task_id, "trial": trial, "attempt": k,
                             "messages": messages, "reply": reply,
                             "raw_reply": raw_reply,
                             "reply_cleanup": g.cleanup,
                             "usage": g.usage, "latency": round(g.latency, 3)},
                            ensure_ascii=False) + "\n")
                if args.strict_replies and g.cleanup:
                    a = _violation_attempt(
                        raw_reply,
                        "strict reply violation: adapter-side cleanup required "
                        + ",".join(g.cleanup),
                    )
                else:
                    cmd, viol = _apply_contract(args.contract, reply)
                    a = (_violation_attempt(reply, viol) if viol else
                         run_attempt(t, cmd, executor=args.executor))
                attempts.append(a)
                if a.passed or k + 1 >= args.max_attempts:
                    break
                messages.append({"role": "assistant", "content": raw_reply})
                messages.append({"role": "user",
                                 "content": _feedback(a, args.contract)})
        # roll per-attempt usage up to a per-task total (what a full attempt costs)
        tot = {"prompt": 0, "completion": 0, "reasoning": 0, "total": 0}
        for m in metas:
            for k2, v in (m.get("usage") or {}).items():
                tot[k2] = tot.get(k2, 0) + v
        return {
            "task_id": t.task_id, "scenario": t.scenario, "tier": t.tier,
            "hazards": t.hazards, "trial": trial,
            "adapter": args.adapter, "model": args.model,
            "contract": args.contract, "max_attempts": args.max_attempts,
            "context_tokens": args.context_tokens,
            "passed": attempts[-1].passed,
            "attempts_used": len(attempts),
            "first_error_class": attempts[0].error_class,
            "usage_total": tot,
            "latency_total": round(sum(m.get("latency", 0) for m in metas), 3),
            "attempts": [dict(dataclasses.asdict(a), raw_reply=rr, **meta)
                         for a, rr, meta in zip(attempts, raw_replies, metas)],
        }

    # adapter-errors are transient (network/quota): they are NEVER written to
    # disk, so --resume reruns exactly those (task,trial) pairs. But that means
    # the run can end with fewer records than targeted — we surface that loudly
    # so a partial run is never mistaken for a complete one (MUST-FIX 3).
    n_done = 0
    n_adapter_err = 0
    consec_adapter_err = 0
    target = args.trials * len(tasks)
    with ThreadPoolExecutor(max_workers=args.parallel) as ex, out.open("a") as f:
        futs = {ex.submit(run_one, trial, t): (trial, t) for trial, t in work}
        for fut in as_completed(futs):
            trial, t = futs[fut]
            rec = fut.result()
            # a (task,trial) is poisoned if ANY attempt hit an adapter error
            # (e.g. retry mode: model-fail then adapter-fail) — skip the whole
            # record so --resume reruns it cleanly (MUST-FIX 3)
            poisoned = any(a["error_class"] == "adapter-error"
                           for a in rec["attempts"])
            with lock:
                n_done += 1
                if poisoned:
                    consec_adapter_err += 1
                    n_adapter_err += 1
                    print(f"[{trial}] {n_done:3d}/{len(work)} "
                          f"{t.task_id:44s} ADAPTER-ERR (not recorded; "
                          "will retry on --resume)", flush=True)
                else:
                    consec_adapter_err = 0
                    records.append(rec)
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    f.flush()
                    status = ("PASS" if rec["passed"]
                              else f"FAIL({rec['first_error_class']})")
                    print(f"[{trial}] {n_done:3d}/{len(work)} "
                          f"{t.task_id:44s} {status}", flush=True)
                if consec_adapter_err >= 6:
                    print("CIRCUIT BREAKER: 6 consecutive adapter errors — "
                          "quota/auth suspected; aborting run (resume later).",
                          flush=True)
                    for other in futs:
                        other.cancel()
                    break

    have = len(records)
    print(f"\n{have}/{target} records on disk -> {out}")
    if n_adapter_err:
        print(f"  {n_adapter_err} adapter-errors this run (not recorded)")
    if have < target:
        print(f"  ⚠ INCOMPLETE: {target - have} (task,trial) pairs missing — "
              f"re-run with --resume before trusting scores below.")
    # authoritative coverage comes from the run config, not the records
    _score(records, expected=target)
    return 0


def cmd_score(args) -> int:
    records = []
    with open(args.results) as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    _score(records)
    return 0


def _all_tasks_by_id():
    return {t.task_id: t for t in (all_tasks() + all_tasks_v03())}


def cmd_replay(args) -> int:
    """Re-execute the STORED model commands from a results file under a
    (possibly different) executor, without re-calling any model — the user's
    insight for B1: tasks are identical, so replaying the same replies on a GNU
    toolchain gives the GNU pass/fail for free. Emits a new results file."""
    by_id = _all_tasks_by_id()
    src = Path(args.results)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    new = []
    with src.open() as f:
        recs = [json.loads(l) for l in f if l.strip()]
    for r in recs:
        t = by_id.get(r["task_id"])
        if t is None:
            continue
        rr = dict(r)
        rr["replayed_executor"] = args.executor
        replayed_attempts = []
        for old in r["attempts"]:
            cmd = old.get("command", "")
            if old.get("error_class") in ("adapter-error", "contract-error"):
                replayed_attempts.append(dict(old))
                continue
            a = run_attempt(t, cmd, executor=args.executor)
            replayed_attempts.append(dict(
                old, command=cmd, exit_code=a.exit_code, stdout=a.stdout,
                stderr=a.stderr, timed_out=a.timed_out, passed=a.passed,
                reason=a.reason, error_class=a.error_class,
                new_files=a.new_files,
            ))
        rr["attempts"] = replayed_attempts
        rr["attempts_used"] = len(replayed_attempts)
        rr["passed"] = replayed_attempts[-1]["passed"]
        rr["first_error_class"] = replayed_attempts[0]["error_class"]
        new.append(rr)
    with out.open("w") as f:
        for r in new:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"replayed {len(new)} records under executor={args.executor} -> {out}")
    _score(new)
    return 0


def _rate(xs) -> str:
    return f"{sum(xs)}/{len(xs)} ({100.0 * sum(xs) / len(xs):5.1f}%)" if xs else "n/a"


def _wilson(k: int, n: int) -> tuple:
    """Wilson 95% CI for a binomial proportion (z=1.96)."""
    if n == 0:
        return (0.0, 0.0)
    z = 1.96
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return (100 * (c - half) / d, 100 * (c + half) / d)


def _score(records, expected: int = 0) -> None:
    if not records:
        print("no records")
        return
    print("\n================ QuoteBench report ================")
    overall = [r["passed"] for r in records]
    ctrl = [r["passed"] for r in records if r["tier"] == 0]
    host = [r["passed"] for r in records if r["tier"] > 0]
    lo, hi = _wilson(sum(overall), len(overall))
    print(f"overall : {_rate(overall)}  [95% CI {lo:.0f}-{hi:.0f}]")
    print(f"controls (tier 0): {_rate(ctrl)}")
    print(f"hostile (tier>0): {_rate(host)}")
    if ctrl and host:
        gap = 100.0 * (sum(ctrl) / len(ctrl) - sum(host) / len(host))
        print(f"QUOTING GAP (control - hostile): {gap:+.1f} pts")

    print("\nper tier:")
    for tier in sorted({r["tier"] for r in records}):
        xs = [r["passed"] for r in records if r["tier"] == tier]
        print(f"  tier {tier}: {_rate(xs)}")

    print("\nper scenario (control | hostile):")
    by_scen = defaultdict(list)
    for r in records:
        by_scen[r["scenario"]].append(r)
    for scen in sorted(by_scen):
        rs = by_scen[scen]
        c = [r["passed"] for r in rs if r["tier"] == 0]
        h = [r["passed"] for r in rs if r["tier"] > 0]
        print(f"  {scen:20s} {_rate(c):>16s} | {_rate(h)}")

    print("\nfirst-attempt failure classes (failed tasks only):")
    cls = defaultdict(int)
    for r in records:
        if not r["passed"]:
            cls[r["first_error_class"]] += 1
    for k in sorted(cls, key=cls.get, reverse=True):
        print(f"  {k:14s} {cls[k]}")

    # marker-comprehension confound: replies that echo the ⟪⟫ delimiters into
    # the command are failing to understand the exact-text convention, NOT
    # failing at quoting — separate them so weak-model quoting skill is legible
    fails = [r for r in records if not r["passed"]]
    marker = [r for r in fails if any(
        "⟪" in (a.get("raw_reply") or "") or "⟫" in (a.get("raw_reply") or "")
        for a in r["attempts"])]
    if marker:
        print(f"\n⚠ MARKER-LEAKAGE: {len(marker)}/{len(fails)} failures leak the "
              f"⟪⟫ delimiters into the command (comprehension, not quoting). "
              f"Quoting-only fail rate excludes these.")

    # dialect confound (I14/B1): a correctly-quoted command that fails only
    # because it uses a GNU idiom on a BSD toolchain (or vice-versa) is a
    # portability failure, not a quoting failure — separate it so BSD/macOS
    # results stay defensible and the Gap can be read quoting-only
    _DIALECT = (
        re.compile(r"sed\s+-i\s+['\"]"),        # GNU sed -i without '' arg (BSD needs -i '')
        re.compile(r"grep\s+-\w*P"),             # grep -P (no BSD PCRE)
        re.compile(r"\bsed\s+-i\s+''"),          # BSD sed -i '' (GNU rejects)
        re.compile(r"readlink\s+-f|date\s+-d|\bmktemp\s+--"),  # common GNU-only flags
    )
    dialect = [r for r in fails if any(
        d.search(a.get("raw_reply") or a.get("command") or "")
        for a in r["attempts"] for d in _DIALECT)]
    if dialect:
        n_dialect_ctrl = sum(1 for r in dialect if r["tier"] == 0)
        print(f"\n⚠ DIALECT: {len(dialect)}/{len(fails)} failures are GNU/BSD "
              f"idiom mismatches (correctly quoted, wrong toolchain dialect) — "
              f"{n_dialect_ctrl} of them are controls. Not quoting failures.")

    excluded = {id(r) for r in marker + dialect}
    if excluded:
        adjusted = [r["passed"] for r in records if id(r) not in excluded]
        print("quoting-only adjusted overall "
              f"(excluding marker/dialect failures): {_rate(adjusted)}")

    cleanup_records = 0
    cleanup_counts = Counter()
    for r in records:
        seen = set()
        for a in r["attempts"]:
            for tag in a.get("reply_cleanup") or []:
                seen.add(tag)
        if seen:
            cleanup_records += 1
            cleanup_counts.update(seen)
    if cleanup_records:
        bits = ", ".join(f"{k}={v}" for k, v in sorted(cleanup_counts.items()))
        print(f"\n⚠ REPLY-CLEANUP: {cleanup_records}/{len(records)} records "
              f"required adapter-side reply cleaning ({bits}). These are "
              "reported separately from task pass/fail because compatibility "
              "cleaning can hide strict contract-adherence failures.")

    # token / latency / cost metrics (present only for API adapters that return
    # usage; a CLI/static run has none)
    usages = [r.get("usage_total") for r in records if r.get("usage_total")
              and r["usage_total"].get("total")]
    if usages:
        n = len(usages)
        avg = {k: sum(u.get(k, 0) for u in usages) / n
               for k in ("prompt", "completion", "reasoning", "total")}
        sums = {k: sum(u.get(k, 0) for u in usages)
                for k in ("prompt", "completion", "reasoning", "total")}
        print("\ntokens/task (mean): "
              f"prompt {avg['prompt']:.0f}  completion {avg['completion']:.0f}"
              f"  reasoning {avg['reasoning']:.0f}  total {avg['total']:.0f}")
        print(f"tokens this run (sum over {n} tasks): "
              f"prompt {sums['prompt']:,}  completion {sums['completion']:,}"
              f"  total {sums['total']:,}")
        lats = [r["latency_total"] for r in records if r.get("latency_total")]
        if lats:
            lats = sorted(lats)
            p50 = lats[len(lats) // 2]
            p95 = lats[min(len(lats) - 1, int(len(lats) * 0.95))]
            print(f"latency/task: mean {sum(lats)/len(lats):.1f}s  "
                  f"p50 {p50:.1f}s  p95 {p95:.1f}s")
        # optional cost: QB_PRICE_IN / QB_PRICE_OUT = $/1M tokens
        pin = float(os.environ.get("QB_PRICE_IN", "0") or 0)
        pout = float(os.environ.get("QB_PRICE_OUT", "0") or 0)
        if pin or pout:
            cost = (sums["prompt"] * pin + sums["completion"] * pout) / 1e6
            per = cost / n
            print(f"cost @ ${pin}/M in, ${pout}/M out: ${cost:.4f} this run "
                  f"(${per:.4f}/task, ${per*len(set(r['task_id'] for r in records)):.4f}/full-pass)")

    # coverage guard (MUST-FIX 3): prefer the authoritative expected count
    # (tasks×trials from the run config, passed in by cmd_run). Fall back to a
    # heuristic against the canonical 56-task set for standalone `score`, which
    # catches wholly-absent tasks that observed-only counting would miss.
    exp = expected
    if not exp:
        trials_set = {r["trial"] for r in records}
        n_trials = max(trials_set) + 1 if trials_set else 0
        seen = {r["task_id"] for r in records}
        canon = {t.task_id for t in all_tasks()}
        # only use the 56-task denominator when this looks like a full run
        # (records ⊆ canon and not an obvious --tasks-filtered subset)
        base = len(canon) if seen <= canon and len(seen) > len(canon) // 2 \
            else len(seen)
        exp = base * n_trials
    if exp and len(records) < exp:
        print(f"\n⚠ COVERAGE: {len(records)}/{exp} (task,trial) cells present "
              f"— {exp - len(records)} missing; rates are on the present "
              "subset only, NOT a full run.")

    trials = sorted({r["trial"] for r in records})
    if len(trials) > 1:
        by_task = defaultdict(dict)
        duplicate_cells = 0
        for r in records:
            task_trials = by_task[r["task_id"]]
            trial = r["trial"]
            if trial in task_trials:
                duplicate_cells += 1
            task_trials[trial] = r["passed"]
        k = len(trials)
        counts = Counter(len(v) for v in by_task.values())
        complete = [all(v.values()) for v in by_task.values() if len(v) == k]
        if len(counts) == 1 and next(iter(counts)) == k and not duplicate_cells:
            print(f"\npass^{k} (task passes in ALL {k} trials): {_rate(complete)}")
        else:
            observed = [all(v.values()) for v in by_task.values()]
            cov = ", ".join(f"{n} task(s) with {m} trial(s)"
                            for m, n in sorted(counts.items()))
            missing = sum(k - len(v) for v in by_task.values())
            print("\nobserved all-pass across available trials "
                  f"(partial multi-trial data): {_rate(observed)}")
            print(f"  observed trial coverage: {cov}")
            if complete:
                print(f"  complete-case pass^{k}: {_rate(complete)}")
            if missing:
                print(f"  ⚠ {missing} trial cell(s) missing; no extra trials "
                      "were imputed or run.")
            if duplicate_cells:
                print(f"  ⚠ {duplicate_cells} duplicate task/trial record(s); "
                      "last record per cell used for observed all-pass.")

    retried = [r for r in records if r["max_attempts"] > 1]
    if retried:
        rec1 = [r["attempts"][0]["passed"] for r in retried]
        reck = [r["passed"] for r in retried]
        print(f"\nretry mode: pass@first {_rate(rec1)} -> pass@final {_rate(reck)}"
              "  (recovery from feedback)")
    print("===================================================")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="quotebench", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("list");  sp.add_argument("--tasks", default="")
    sp.add_argument("--suite", default="core", choices=["core", "v03", "l2", "all"])
    sp.set_defaults(fn=cmd_list)

    sp = sub.add_parser("show");  sp.add_argument("task_id")
    sp.set_defaults(fn=cmd_show)

    sp = sub.add_parser("export")
    sp.add_argument("--suite", default="core", choices=["core", "v03", "l2", "all"])
    sp.add_argument("--out", default="tasks/manifest.jsonl")
    sp.add_argument("--tasks", default="")
    sp.set_defaults(fn=cmd_export)

    sp = sub.add_parser("validate")
    sp.add_argument("--suite", default="core", choices=["core", "v03", "l2", "all"])
    sp.add_argument("--executor", default="local", choices=["local", "docker"])
    sp.add_argument("--contract", default="raw", choices=["raw", "json", "wrapped"])
    sp.add_argument("--tasks", default="")
    sp.set_defaults(fn=cmd_validate)

    sp = sub.add_parser("run")
    sp.add_argument("--suite", default="core", choices=["core", "v03", "l2", "all"])
    sp.add_argument("--adapter", required=True,
                    choices=["oracle", "naive", "anthropic", "openai",
                             "azure", "gemini", "agy", "cmd", "file"])
    sp.add_argument("--min-interval", type=float, default=0.0,
                    help="global min seconds between API calls (RPM guard)")
    sp.add_argument("--reasoning-effort", default="",
                    help="azure: reasoning_effort; gemini: thinkingLevel")
    sp.add_argument("--model", default="")
    sp.add_argument("--adapter-cmd", default="")
    sp.add_argument("--file", default="")
    sp.add_argument("--mode", default="oneshot", choices=["oneshot", "retry"])
    sp.add_argument("--max-attempts", type=int, default=1)
    sp.add_argument("--trials", type=int, default=1)
    sp.add_argument("--contract", default="raw", choices=["raw", "json", "wrapped"])
    sp.add_argument("--context-tokens", type=int, default=0,
                    help="L2: prepend N tokens of realistic session context")
    sp.add_argument("--parallel", type=int, default=1)
    sp.add_argument("--resume", action="store_true")
    sp.add_argument("--executor", default="local", choices=["local", "docker"])
    sp.add_argument("--strict-replies", action="store_true",
                    help="treat markdown fences, shell prompts, <think> wrappers, "
                         "or other adapter-side reply cleanup as contract-error")
    sp.add_argument("--tasks", default="")
    sp.add_argument("--out", default="results/run.jsonl")
    sp.set_defaults(fn=cmd_run)

    sp = sub.add_parser("score");  sp.add_argument("results")
    sp.set_defaults(fn=cmd_score)

    sp = sub.add_parser("replay")  # re-execute stored commands under another executor
    sp.add_argument("results")
    sp.add_argument("--executor", default="docker", choices=["local", "docker"])
    sp.add_argument("--out", required=True)
    sp.set_defaults(fn=cmd_replay)

    sp = sub.add_parser("prompt")  # print the system prompt (for cmd adapters)
    sp.set_defaults(fn=lambda a: (print(SYSTEM_PROMPT), 0)[1])

    args = p.parse_args(argv)
    if getattr(args, "mode", None) == "retry" and args.max_attempts < 2:
        args.max_attempts = 3
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
