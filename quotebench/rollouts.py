"""Verification and descriptive summaries for the public rollout archive."""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

SUPPORTED_SCHEMA_KEYS = {
    "$schema", "$id", "title", "description", "type", "required",
    "additionalProperties", "properties", "items", "enum", "const",
    "minimum", "maximum", "minLength", "maxLength", "minItems",
    "maxItems", "pattern",
}
CHECKSUM_RE = re.compile(r"^([0-9a-f]{64})  ([^\n]+)$")


def _digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def validate_schema(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    unsupported = set(schema) - SUPPORTED_SCHEMA_KEYS
    if unsupported:
        raise ValueError(f"{path}: unsupported schema keywords {sorted(unsupported)}")
    declared = schema.get("type")
    if declared is not None:
        allowed = {declared} if isinstance(declared, str) else set(declared)
        actual = _type_name(value)
        if actual not in allowed and not (actual == "integer" and "number" in allowed):
            raise ValueError(f"{path}: expected {sorted(allowed)}, got {actual}")
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"{path}: constant mismatch")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: value is outside the declared enum")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        missing = [key for key in schema.get("required", []) if key not in value]
        if missing:
            raise ValueError(f"{path}: missing required fields {missing}")
        if schema.get("additionalProperties") is False:
            extra = sorted(set(value) - set(properties))
            if extra:
                raise ValueError(f"{path}: undeclared fields {extra}")
        for key, child in value.items():
            if key in properties:
                validate_schema(child, properties[key], f"{path}.{key}")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise ValueError(f"{path}: too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ValueError(f"{path}: too many items")
        if "items" in schema:
            for index, child in enumerate(value):
                validate_schema(child, schema["items"], f"{path}[{index}]")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise ValueError(f"{path}: string is shorter than minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ValueError(f"{path}: string is longer than maxLength")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            raise ValueError(f"{path}: string does not match the declared pattern")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"{path}: value is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"{path}: value is above maximum")


def _records(root: Path) -> Iterable[tuple[str, int, dict[str, Any]]]:
    for file in sorted(root.glob("*/*.jsonl")):
        relative = file.relative_to(root).as_posix()
        for line_number, line in enumerate(file.read_text().splitlines(), 1):
            if line.strip():
                yield relative, line_number, json.loads(line)


def verify_rollouts(root: Path) -> dict[str, Any]:
    root = root.resolve()
    checksum_path = root / "SHA256SUMS"
    if not checksum_path.is_file():
        raise ValueError("missing SHA256SUMS")
    checksums: dict[str, str] = {}
    for line_number, line in enumerate(checksum_path.read_text().splitlines(), 1):
        match = CHECKSUM_RE.fullmatch(line)
        if not match:
            raise ValueError(f"SHA256SUMS:{line_number}: malformed line")
        checksum, name = match.groups()
        candidate = Path(name)
        if candidate.is_absolute() or ".." in candidate.parts or name in checksums:
            raise ValueError(f"SHA256SUMS:{line_number}: unsafe or duplicate path")
        checksums[name] = checksum
    inventory = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*") if path.is_file()
    }
    expected = set(checksums) | {"SHA256SUMS"}
    extra = inventory - expected - {".gitattributes"}
    missing = expected - inventory
    if missing or extra:
        raise ValueError(f"inventory mismatch: missing={sorted(missing)}, extra={sorted(extra)}")
    for name, checksum in checksums.items():
        if _digest(root / name) != checksum:
            raise ValueError(f"checksum mismatch: {name}")

    schema = json.loads((root / "rollout-schema.json").read_text())
    manifest = json.loads((root / "MANIFEST.json").read_text())
    jsonl = {name for name in checksums if name.endswith(".jsonl")}
    if set(manifest.get("files", {})) != jsonl:
        raise ValueError("manifest and checksum JSONL inventories differ")
    if not manifest.get("gates") or not all(manifest["gates"].values()):
        raise ValueError("manifest contains a failed or missing release gate")
    rows = 0
    per_file: dict[str, int] = defaultdict(int)
    for name, line_number, record in _records(root):
        try:
            validate_schema(record, schema)
        except ValueError as exc:
            raise ValueError(f"{name}:{line_number}: {exc}") from exc
        rows += 1
        per_file[name] += 1
    if rows != manifest.get("total_rollouts"):
        raise ValueError(f"rollout total mismatch: observed={rows}")
    for name, detail in manifest["files"].items():
        if per_file[name] != detail.get("rollouts") or not detail.get("complete"):
            raise ValueError(f"manifest count/completeness mismatch: {name}")
    return {"files": len(inventory), "arm_files": len(jsonl), "rollouts": rows}


def summarize_rollouts(root: Path) -> dict[str, Any]:
    verification = verify_rollouts(root)
    cells: dict[tuple[Any, ...], list[int]] = defaultdict(lambda: [0, 0])
    mechanism: dict[str, dict[str, list[int]]] = defaultdict(
        lambda: defaultdict(lambda: [0, 0])
    )
    for _, _, record in _records(root.resolve()):
        sampling = record["sampling"]
        model_id = record["model"]["model_id"]
        generation = sampling["contract"]
        trial = sampling["trial"]
        effort = sampling.get("effort")
        for execution in record["executions"]:
            key = (
                record["campaign"], model_id, generation, effort, trial,
                execution["target_contract"], execution["toolchain"],
            )
            cells[key][0] += int(execution["passed"])
            cells[key][1] += 1
            if (record["campaign"] == "raw-vs-nested" and trial == 0
                    and execution["toolchain"] == "gnu"):
                label = {
                    ("raw", "raw"): "RR", ("raw", "nested"): "RN",
                    ("nested", "raw"): "NR", ("nested", "nested"): "NN",
                }.get((generation, execution["target_contract"]))
                if label:
                    mechanism[model_id][label][0] += int(execution["passed"])
                    mechanism[model_id][label][1] += 1

    rates = []
    for key, (passed, total) in sorted(cells.items(), key=lambda item: tuple(str(v) for v in item[0])):
        campaign, model, generation, effort, trial, target, toolchain = key
        rates.append({
            "campaign": campaign, "model_id": model,
            "generation_contract": generation, "effort": effort,
            "trial": trial, "target_contract": target, "toolchain": toolchain,
            "passed": passed, "total": total,
            "pass_rate_pct": round(100 * passed / total, 1),
        })

    mechanism_rows = []
    for model, values in sorted(mechanism.items()):
        row: dict[str, Any] = {"model_id": model}
        counts: dict[str, int] = {}
        for label in ("RR", "RN", "NR", "NN"):
            passed, total = values[label]
            if total != 56:
                raise ValueError(f"{model}/{label}: expected 56 GNU trial-0 outcomes")
            counts[label] = passed
            row[label] = round(100 * passed / total, 1)
        row["damage_pp"] = round(100 * (counts["RN"] - counts["RR"]) / 56, 1)
        row["compensation_pp"] = round(100 * (counts["NN"] - counts["RN"]) / 56, 1)
        row["matched_gap_pp"] = round(100 * (counts["NN"] - counts["RR"]) / 56, 1)
        mechanism_rows.append(row)
    comparable = reversals = 0
    for index, left in enumerate(mechanism_rows):
        for right in mechanism_rows[index + 1:]:
            raw_delta = left["RR"] - right["RR"]
            nested_delta = left["NN"] - right["NN"]
            if raw_delta and nested_delta:
                comparable += 1
                reversals += int(raw_delta * nested_delta < 0)
    return {
        "schema": "quotebench-public-analysis-v1",
        "verification": verification,
        "mechanism": {
            "rows": mechanism_rows,
            "strict_reversals": reversals,
            "comparable_pairs": comparable,
        },
        "rates": rates,
    }
