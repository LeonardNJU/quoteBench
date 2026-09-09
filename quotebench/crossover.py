"""Contract-crossover helpers for QuoteBench.

The controlled crossover replays an already generated model reply through both
the raw and nested-shell execution contracts. It separates two components that
are confounded in a conventional two-arm comparison:

1. generation adaptation: the model may emit a different reply after seeing a
   different contract prompt;
2. transport sensitivity: the same reply may behave differently when an extra
   ``bash -c "..."`` interpolation layer is imposed.

No model is called by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


SUPPORTED_SOURCE_CONTRACTS = {"raw", "nested-shell", "nested-shell-v2", "wrapped"}
TARGET_CONTRACTS = ("raw", "nested-shell")
# Additional replay-only transports for main-conference robustness analysis.
# They never define generation contracts and therefore do not enter the historical
# 2x2 RR/RN/NR/NN estimand.
STRESS_TRANSPORTS = (
    "raw",
    "nested-shell",
    "single-quoted-shell",
    "double-nested-shell",
)


@dataclass(frozen=True)
class RecordKey:
    task_id: str
    trial: int


def canonical_contract(value: str) -> str:
    """Return the canonical Study-A contract name."""
    if value == "wrapped":
        return "nested-shell"
    if value == "nested-shell-v2":
        # v2 shares the nested transport; only the generation prompt differs.
        return "nested-shell"
    if value not in {"raw", "nested-shell"}:
        raise ValueError(
            f"contract crossover supports raw/nested-shell records, got {value!r}"
        )
    return value


def record_key(record: Mapping[str, Any]) -> RecordKey:
    return RecordKey(str(record["task_id"]), int(record.get("trial", 0)))


def terminal_attempt(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the attempt whose outcome defines the record.

    QuoteBench records score the final attempt. Study A is normally one-shot,
    but using the terminal attempt keeps the replay faithful if a diagnostic run
    used repair feedback.
    """
    attempts = record.get("attempts")
    if not isinstance(attempts, Sequence) or isinstance(attempts, (str, bytes)):
        raise ValueError(f"record {record_key(record)} has no attempts list")
    if not attempts:
        raise ValueError(f"record {record_key(record)} has an empty attempts list")
    attempt = attempts[-1]
    if not isinstance(attempt, Mapping):
        raise ValueError(f"record {record_key(record)} has a malformed attempt")
    return attempt


def generated_reply(
    record: Mapping[str, Any], source_contract: str | None = None
) -> str:
    """Recover the exact model reply before the execution contract was applied.

    Generation records written by the public ``run`` command carry the reply at
    top level. Private runner records carry it in the terminal attempt. Early
    Study-A raw records predate ``raw_reply`` retention. For those records,
    the executed command is exactly the raw reply by construction, so the
    command itself is a lossless fallback. A legacy nested record can likewise
    be inverted only when it has the exact harness prefix/suffix.
    """
    if isinstance(record.get("reply"), str):
        return str(record["reply"])
    attempt = terminal_attempt(record)
    reply = attempt.get("raw_reply")
    if isinstance(reply, str):
        return reply

    contract_value = source_contract or record.get("contract")
    if not isinstance(contract_value, str):
        raise ValueError(
            f"record {record_key(record)} lacks raw_reply and source contract"
        )
    contract = canonical_contract(contract_value)
    command = attempt.get("command")
    if not isinstance(command, str):
        raise ValueError(
            f"record {record_key(record)} lacks both raw_reply and command"
        )
    if contract == "raw":
        return command
    prefix, suffix = 'bash -c "', '"'
    if command.startswith(prefix) and command.endswith(suffix):
        return command[len(prefix):-len(suffix)]
    raise ValueError(
        f"record {record_key(record)} has no lossless nested-shell reply recovery"
    )


def command_for_transport(reply: str, target_transport: str) -> str:
    """Apply one execution transport to a fixed generated reply.

    ``single-quoted-shell`` interpolates the reply without escaping into one
    outer single-quoted shell argument. ``double-nested-shell`` adds two
    double-quoted ``bash -c`` boundaries. These are controlled stressors: the
    deliberate lack of escaping is the intervention being measured.
    """
    if target_transport == "raw":
        return reply
    if target_transport == "nested-shell":
        return 'bash -c "' + reply + '"'
    if target_transport == "single-quoted-shell":
        return "bash -c '" + reply + "'"
    if target_transport == "double-nested-shell":
        backslash = chr(92)
        return f'bash -c "bash -c {backslash}"{reply}{backslash}""'
    raise ValueError(f"unsupported execution transport: {target_transport!r}")


def command_for(reply: str, target_contract: str) -> str:
    """Apply one historical raw/nested target transport to a fixed reply."""
    target_contract = canonical_contract(target_contract)
    return command_for_transport(reply, target_contract)


def index_records(
    records: Iterable[Mapping[str, Any]], expected_contract: str
) -> dict[RecordKey, Mapping[str, Any]]:
    """Index records and reject duplicate or mislabeled cells."""
    expected_contract = canonical_contract(expected_contract)
    out: dict[RecordKey, Mapping[str, Any]] = {}
    for record in records:
        actual = canonical_contract(str(record.get("contract", expected_contract)))
        if actual != expected_contract:
            raise ValueError(
                f"expected {expected_contract} record, found {actual} at "
                f"{record_key(record)}"
            )
        key = record_key(record)
        if key in out:
            raise ValueError(f"duplicate record key: {key}")
        generated_reply(record, expected_contract)
        out[key] = record
    return out


def paired_keys(
    raw_records: Iterable[Mapping[str, Any]],
    nested_records: Iterable[Mapping[str, Any]],
    *,
    allow_unpaired: bool = False,
) -> tuple[
    dict[RecordKey, Mapping[str, Any]],
    dict[RecordKey, Mapping[str, Any]],
    list[RecordKey],
]:
    """Return validated raw/nested indexes and their paired keys."""
    raw = index_records(raw_records, "raw")
    nested = index_records(nested_records, "nested-shell")
    raw_keys, nested_keys = set(raw), set(nested)
    if not allow_unpaired and raw_keys != nested_keys:
        only_raw = sorted(raw_keys - nested_keys, key=lambda k: (k.task_id, k.trial))
        only_nested = sorted(
            nested_keys - raw_keys, key=lambda k: (k.task_id, k.trial)
        )
        raise ValueError(
            "unpaired crossover inputs: "
            f"{len(only_raw)} raw-only and {len(only_nested)} nested-only cells"
        )
    keys = sorted(raw_keys & nested_keys, key=lambda k: (k.task_id, k.trial))
    if not keys:
        raise ValueError("crossover inputs have no paired cells")
    return raw, nested, keys


def summarize_crossover(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize the 2x2 generation-by-transport replay.

    Effects use percentage points and a consistent intervention-minus-baseline
    sign convention.
    """
    cells: dict[tuple[str, str], list[bool]] = {
        (source, target): []
        for source in ("raw", "nested-shell")
        for target in TARGET_CONTRACTS
    }
    for row in rows:
        key = (
            canonical_contract(str(row["source_contract"])),
            canonical_contract(str(row["target_contract"])),
        )
        cells[key].append(bool(row["passed"]))

    def rate(source: str, target: str) -> float:
        values = cells[(source, target)]
        if not values:
            raise ValueError(f"missing crossover cell {source} -> {target}")
        return 100.0 * sum(values) / len(values)

    rates = {
        f"{source}_generated__{target}_transport": rate(source, target)
        for source in ("raw", "nested-shell")
        for target in TARGET_CONTRACTS
    }
    rr = rate("raw", "raw")
    rn = rate("raw", "nested-shell")
    nr = rate("nested-shell", "raw")
    nn = rate("nested-shell", "nested-shell")
    effects = {
        "transport_effect_on_raw_generation": rn - rr,
        "transport_effect_on_nested_generation": nn - nr,
        "generation_adaptation_under_raw_transport": nr - rr,
        "generation_adaptation_under_nested_transport": nn - rn,
        "generation_by_transport_interaction": (nn - nr) - (rn - rr),
        "observed_nested_minus_raw": nn - rr,
    }
    return {
        "rates_pct": rates,
        "effects_pp": effects,
        "n_per_cell": {
            f"{source}_generated__{target}_transport": len(cells[(source, target)])
            for source in ("raw", "nested-shell")
            for target in TARGET_CONTRACTS
        },
    }
