"""Create a deterministic, stratified size-reduced copy of the Poker v1 suite."""
from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import os
import shutil
import tempfile
from collections import Counter
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from pathlib import Path

from kev.suite import digest, read_manifest, write_json

SPLITS = ("train", "calibration", "development", "test")
DATASET_REVISION = "7ac61f961c81a50fc0f667820b2fb0e432dfec0d"


def round_half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def stratum_for(record: dict) -> str:
    try:
        meta = record["_meta"]
        street = meta["candidate_policy"]["street"]
        label = record["questions"]["action"]["label"]
    except (KeyError, TypeError) as error:
        raise ValueError("Poker record is missing candidate_policy.street or action label") from error
    if not isinstance(street, str) or not street or not isinstance(label, str) or not label:
        raise ValueError("Poker record has an empty street or action label")
    action_type = label.split("_to_", 1)[0]
    return f"{street}|{action_type}"


def allocate_quotas(stratum_counts: dict[str, int], fraction: Decimal) -> tuple[dict[str, int], int]:
    if not Decimal(0) < fraction <= Decimal(1):
        raise ValueError("fraction must be in (0, 1]")
    total = sum(stratum_counts.values())
    target = round_half_up(Decimal(total) * fraction)
    quotas = {key: int((Decimal(count) * fraction).to_integral_value(rounding=ROUND_FLOOR))
              for key, count in stratum_counts.items()}
    left = target - sum(quotas.values())
    order = sorted(
        stratum_counts,
        key=lambda key: (-(Decimal(stratum_counts[key]) * fraction - quotas[key]), key),
    )
    for key in order[:left]:
        quotas[key] += 1
    return quotas, target


def _record_from_line(line: bytes, source: str, line_number: int) -> dict:
    try:
        record = json.loads(line)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError(f"{source}:{line_number}: invalid JSONL record") from error
    if not isinstance(record, dict):
        raise ValueError(f"{source}:{line_number}: record must be an object")
    return record


def _scan_source(path: Path) -> tuple[dict[str, int], int, str]:
    counts: Counter[str] = Counter()
    records = 0
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for line_number, line in enumerate(stream, 1):
            sha.update(line)
            if not line.strip():
                continue
            record = _record_from_line(line, str(path), line_number)
            counts[stratum_for(record)] += 1
            records += 1
    return dict(counts), records, sha.hexdigest()


def _select_lines(path: Path, expected_hash: str, expected_records: int, quotas: dict[str, int], seed: int) -> dict[str, list[tuple[int, bytes]]]:
    heaps: dict[str, list[tuple[int, int, bytes]]] = {key: [] for key in quotas}
    sha = hashlib.sha256()
    records = 0
    with path.open("rb") as stream:
        for line_number, line in enumerate(stream, 1):
            sha.update(line)
            if not line.strip():
                continue
            record = _record_from_line(line, str(path), line_number)
            stratum = stratum_for(record)
            if stratum not in quotas:
                raise ValueError(f"source changed between passes; new stratum {stratum!r}")
            row_id = record.get("_meta", {}).get("id")
            if not isinstance(row_id, str) or not row_id:
                raise ValueError(f"{path}:{line_number}: record has no stable _meta.id")
            score = int(hashlib.sha256(f"{seed}:{row_id}".encode("utf-8")).hexdigest(), 16)
            quota = quotas[stratum]
            if quota == 0:
                records += 1
                continue
            heap = heaps[stratum]
            item = (-score, -line_number, line)
            if len(heap) < quota:
                heapq.heappush(heap, item)
            elif item > heap[0]:
                heapq.heapreplace(heap, item)
            records += 1
    if records != expected_records or sha.hexdigest() != expected_hash:
        raise ValueError(f"source changed during sampling: {path}")
    selected = {}
    for stratum, heap in heaps.items():
        if len(heap) != quotas[stratum]:
            raise AssertionError(f"selected {len(heap)} rows for {stratum}, expected {quotas[stratum]}")
        selected[stratum] = sorted(((-negative_line, raw) for _, negative_line, raw in heap), key=lambda item: item[0])
    return selected


def sample_partition(source_path: Path, output_path: Path, expected: dict, fraction: Decimal, seed: int) -> dict:
    stratum_counts, records, actual_hash = _scan_source(source_path)
    if (actual_hash != expected.get("sha256") or records != expected.get("records")
            or (expected.get("bytes") is not None and source_path.stat().st_size != expected["bytes"])):
        raise ValueError(f"parent partition hash/count differs from manifest: {source_path}")
    quotas, target = allocate_quotas(stratum_counts, fraction)
    selected = _select_lines(source_path, actual_hash, records, quotas, seed)
    chosen = sorted((row_number, raw) for rows in selected.values() for row_number, raw in rows)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as stream:
        for _, raw in chosen:
            stream.write(raw)
    output_hash = digest(output_path)
    output_records = len(chosen)
    if output_records != target:
        raise AssertionError(f"selected {output_records} rows, expected partition target {target}")
    return {
        "source_records": records, "selected_records": output_records,
        "source_sha256": actual_hash, "output_sha256": output_hash,
        "strata": {
            key: {"source_records": stratum_counts[key], "quota": quotas[key],
                  "exact_quota": str(Decimal(stratum_counts[key]) * fraction)}
            for key in sorted(stratum_counts)
        },
    }


def build_suite(parent: Path, out: Path, fraction: Decimal = Decimal("0.05"), seed: int = 42) -> dict:
    if not Decimal(0) < fraction <= Decimal(1):
        raise ValueError("fraction must be in (0, 1]")
    parent, out = parent.resolve(), out.resolve()
    if out == parent or parent in out.parents:
        raise ValueError("output suite must be separate from the parent suite")
    if out.exists():
        raise FileExistsError(f"refusing to overwrite existing suite path: {out}")

    parent_manifest_path = parent / "manifest.json"
    if not parent_manifest_path.is_file():
        raise FileNotFoundError(parent_manifest_path)
    parent_manifest = read_manifest(parent)
    if parent_manifest.get("suite") != "poker-v1":
        raise ValueError("parent suite must be evals/poker-v1")
    source_files = parent_manifest.get("files", {})
    for split in SPLITS:
        if f"{split}.jsonl" not in source_files:
            raise ValueError(f"parent manifest has no {split}.jsonl partition")

    out.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{out.name}-", dir=out.parent))
    try:
        split_stats = {}
        output_files = {}
        for split in SPLITS:
            source_path = parent / f"{split}.jsonl"
            output_path = staging / f"{split}.jsonl"
            split_stats[split] = sample_partition(source_path, output_path, source_files[f"{split}.jsonl"], fraction, seed)
            output_files[f"{split}.jsonl"] = {
                "sha256": split_stats[split]["output_sha256"],
                "bytes": output_path.stat().st_size,
                "records": split_stats[split]["selected_records"],
            }

        report = {
            "parent_suite": "evals/poker-v1",
            "parent_manifest_sha256": digest(parent_manifest_path),
            "fraction": str(fraction), "seed": seed,
            "selection_algorithm": "per-partition largest-remainder quotas over candidate_policy.street and action label prefix; choose lowest SHA256(seed:id), stable source-order output",
            "rounding": "partition target uses ROUND_HALF_UP; stratum quotas use floors plus largest fractional remainders, lexicographic stratum tie break",
            "partitions": split_stats,
            "test_is_non_independent_subset": True,
            "model_evaluation_run": False,
        }
        write_json(staging / "selection-report.json", report)
        readme = f"""# Poker v1 lite

This suite selects {fraction:.1%} of each partition from `evals/poker-v1` using seed {seed}. The selection strata are `candidate_policy.street` and the action type from the final label. Each partition is sampled independently. The builder copies selected JSONL lines as raw bytes, preserving the original records, metadata, and within-partition order.

| Partition | Parent rows | Selected rows |
| --- | ---: | ---: |
"""
        for split in SPLITS:
            readme += f"| {split} | {split_stats[split]['source_records']:,} | {split_stats[split]['selected_records']:,} |\n"
        readme += """

The test rows are a deterministic subset of the existing locked test. They are not an independent test set. No model evaluation was run. Rebuild the parent from its pinned PokerBench source files before using this builder if `evals/poker-v1` is unavailable.

To rebuild into a fresh directory, run:

```sh
uv run python scripts/build_poker_lite.py --parent evals/poker-v1 --out /tmp/poker-v1-lite-rebuilt --fraction 0.05 --seed 42
```

For training, keep the reviewed candidate set by passing `--p_none 0 --p_none_distract 0 --p_distract 0 --p_none_pair 0`. These flags do not alter Kev's CLI defaults.
"""
        (staging / "README.md").write_text(readme, encoding="utf-8")
        output_files["selection-report.json"] = {"sha256": digest(staging / "selection-report.json"), "bytes": (staging / "selection-report.json").stat().st_size}
        output_files["README.md"] = {"sha256": digest(staging / "README.md"), "bytes": (staging / "README.md").stat().st_size}

        manifest = {
            "suite": "poker-v1-lite", "version": 1,
            "dataset": parent_manifest.get("dataset"), "dataset_revision": parent_manifest.get("dataset_revision", DATASET_REVISION),
            "license": parent_manifest.get("license"), "eval_only": False,
            "trainable_sources": parent_manifest.get("trainable_sources", []),
            "eval_only_sources": parent_manifest.get("eval_only_sources", []),
            "holdout_sources": parent_manifest.get("holdout_sources", []),
            "context": parent_manifest.get("context", {}),
            "training_context": parent_manifest.get("training_context", {}),
            "partition_context": parent_manifest.get("partition_context", {}),
            "base_revisions": parent_manifest.get("base_revisions", {}),
            "parent_suite": "evals/poker-v1", "parent_manifest_sha256": digest(parent_manifest_path),
            "parent_partition_sha256": {f"{split}.jsonl": source_files[f"{split}.jsonl"]["sha256"] for split in SPLITS},
            "selection": {"fraction": str(fraction), "seed": seed, "stratify_by": ["candidate_policy.street", "action_label_type"],
                          "algorithm": "sha256(f'{seed}:{_meta.id}'), lowest hashes within largest-remainder quotas; selected raw lines emitted in source order"},
            "counts": {split: split_stats[split]["selected_records"] for split in SPLITS},
            "protocol": {"test_is_non_independent_subset": True, "model_evaluation_run": False,
                         "training_run": False, "raw_records_unchanged": True},
            "files": output_files,
        }
        write_json(staging / "manifest.json", manifest)
        # Fail if another process created the destination after the initial check.
        os.rename(staging, out)
        return manifest
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a deterministic reduced-size Poker v1 suite.")
    parser.add_argument("--parent", type=Path, default=Path("evals/poker-v1"))
    parser.add_argument("--out", type=Path, default=Path("evals/poker-v1-lite"))
    parser.add_argument("--fraction", type=Decimal, default=Decimal("0.05"))
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    manifest = build_suite(args.parent, args.out, args.fraction, args.seed)
    print(json.dumps({"suite": manifest["suite"], "counts": manifest["counts"], "out": str(args.out)}, indent=2))


if __name__ == "__main__":
    main()
