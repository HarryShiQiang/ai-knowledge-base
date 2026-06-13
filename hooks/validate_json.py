#!/usr/bin/env python3
"""Validate knowledge article JSON files.

Supports single-file and multi-file (glob pattern) input.
Exits 0 on success, 1 on failure with error details and summary.

Usage:
    python hooks/validate_json.py <json_file> [json_file2 ...]
    python hooks/validate_json.py knowledge/articles/*.json
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

REQUIRED_FIELDS: dict[str, type] = {
    "id": str,
    "title": str,
    "source_url": str,
    "summary": str,
    "tags": list,
    "status": str,
}

VALID_STATUSES = frozenset({"draft", "review", "published", "archived"})

VALID_AUDIENCES = frozenset({"beginner", "intermediate", "advanced"})

ID_PATTERN = re.compile(
    r"^\d{8}-[a-z]{2,3}-[a-z0-9][a-z0-9-]*$"
)

URL_PATTERN = re.compile(r"^https?://\S+$")

MIN_SUMMARY_LENGTH = 20

SCORE_MIN = 1
SCORE_MAX = 10


def _expand_paths(raw_args: list[str]) -> list[Path]:
    """Expand glob patterns and collect unique .json file paths."""
    seen: set[Path] = set()
    result: list[Path] = []

    for arg in raw_args:
        path = Path(arg)
        if "*" in arg or "?" in arg or "[" in arg:
            parent = path.parent if path.parent != Path(".") else Path()
            matches = sorted(parent.glob(path.name)) if parent != Path() else sorted(Path().glob(arg))
            for m in matches:
                resolved = m.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    result.append(resolved)
        else:
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                result.append(resolved)

    return result


def _read_json(filepath: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Read and parse a JSON file. Returns (data, error_message)."""
    try:
        raw = filepath.read_text(encoding="utf-8")
    except OSError as exc:
        return None, f"Cannot read file: {exc}"

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"Invalid JSON: {exc}"

    if not isinstance(data, dict):
        return None, "Root element is not a JSON object (dict)"

    return data, None


def _validate_required_fields(data: dict[str, Any], filepath: Path) -> list[str]:
    """Check required fields exist and have correct types."""
    errors: list[str] = []
    for field, expected_type in REQUIRED_FIELDS.items():
        if field not in data:
            errors.append(f"Missing required field: {field!r}")
            continue

        value = data[field]
        if not isinstance(value, expected_type):
            actual = type(value).__name__
            expected = expected_type.__name__
            errors.append(
                f"Field {field!r}: expected {expected}, got {actual}"
            )

    return errors


def _validate_id(value: str, filepath: Path) -> list[str]:
    """Validate ID format: {YYYYMMDD}-{source}-{slug}."""
    errors: list[str] = []
    if not isinstance(value, str):
        return errors

    if not ID_PATTERN.match(value):
        errors.append(
            f"Invalid ID format {value!r}: expected pattern "
            f"<YYYYMMDD>-<source>-<slug> (e.g. 20260613-gh-llama3-vision)"
        )
        return errors

    date_str = value[:8]
    try:
        year = int(date_str[:4])
        month = int(date_str[4:6])
        day = int(date_str[6:8])
        if not (2020 <= year <= 2099 and 1 <= month <= 12 and 1 <= day <= 31):
            raise ValueError
    except (ValueError, IndexError):
        errors.append(f"Invalid date in ID {value!r}: {date_str}")

    return errors


def _validate_status(value: str, filepath: Path) -> list[str]:
    """Validate status is one of the allowed values."""
    if not isinstance(value, str):
        return []
    if value not in VALID_STATUSES:
        allowed = ", ".join(sorted(VALID_STATUSES))
        return [f"Invalid status {value!r}: must be one of {allowed}"]
    return []


def _validate_url(value: str, filepath: Path) -> list[str]:
    """Validate source_url format."""
    if not isinstance(value, str):
        return []
    if not URL_PATTERN.match(value):
        return [f"Invalid URL format: {value!r}"]
    return []


def _validate_summary(value: str, filepath: Path) -> list[str]:
    """Check summary minimum length."""
    if not isinstance(value, str):
        return []
    if len(value) < MIN_SUMMARY_LENGTH:
        return [
            f"Summary too short: {len(value)} chars, "
            f"minimum {MIN_SUMMARY_LENGTH}"
        ]
    return []


def _validate_tags(value: list, filepath: Path) -> list[str]:
    """Check tags has at least 1 element and all items are strings."""
    errors: list[str] = []
    if not isinstance(value, list):
        return errors

    if len(value) < 1:
        errors.append("Tags list is empty: at least 1 tag is required")
        return errors

    for idx, item in enumerate(value):
        if not isinstance(item, str):
            errors.append(
                f"Tags[{idx}]: expected str, got {type(item).__name__}"
            )
    return errors


def _validate_score(data: dict[str, Any], filepath: Path) -> list[str]:
    """Check score (if present) is within 1-10 range."""
    if "score" not in data:
        return []

    value = data["score"]
    if not isinstance(value, (int, float)):
        return [f"Field 'score': expected int/float, got {type(value).__name__}"]

    if isinstance(value, bool):
        return [f"Field 'score': expected int/float, got bool"]

    if not (SCORE_MIN <= value <= SCORE_MAX):
        return [
            f"Field 'score': value {value} out of range "
            f"[{SCORE_MIN}, {SCORE_MAX}]"
        ]
    return []


def _validate_audience(data: dict[str, Any], filepath: Path) -> list[str]:
    """Check audience (if present) is a valid value."""
    if "audience" not in data:
        return []

    value = data["audience"]
    if not isinstance(value, str):
        return [f"Field 'audience': expected str, got {type(value).__name__}"]

    if value not in VALID_AUDIENCES:
        allowed = ", ".join(sorted(VALID_AUDIENCES))
        return [
            f"Invalid audience {value!r}: must be one of {allowed}"
        ]
    return []


def validate_file(filepath: Path) -> list[str]:
    """Validate a single JSON file. Returns list of error messages."""
    errors: list[str] = []

    data, read_error = _read_json(filepath)
    if read_error:
        errors.append(read_error)
        return errors

    errors.extend(_validate_required_fields(data, filepath))
    errors.extend(_validate_id(data.get("id", ""), filepath))
    errors.extend(_validate_status(data.get("status", ""), filepath))
    errors.extend(_validate_url(data.get("source_url", ""), filepath))
    errors.extend(_validate_summary(data.get("summary", ""), filepath))
    errors.extend(_validate_tags(data.get("tags", []), filepath))
    errors.extend(_validate_score(data, filepath))
    errors.extend(_validate_audience(data, filepath))

    return errors


def main() -> int:
    """Entry point. Returns 0 on success, 1 on failure."""
    if len(sys.argv) < 2:
        print(
            "Usage: python hooks/validate_json.py <json_file> [json_file2 ...]",
            file=sys.stderr,
        )
        print(
            "       python hooks/validate_json.py knowledge/articles/*.json",
            file=sys.stderr,
        )
        return 1

    raw_args = sys.argv[1:]
    filepaths = _expand_paths(raw_args)

    if not filepaths:
        print(f"Error: no matching JSON files found for: {' '.join(raw_args)}", file=sys.stderr)
        return 1

    total_files = len(filepaths)
    passed_files = 0
    failed_files = 0
    total_errors = 0
    all_errors: dict[str, list[str]] = {}

    print(f"Validating {total_files} file(s)...\n")

    for filepath in filepaths:
        errors = validate_file(filepath)
        if errors:
            failed_files += 1
            total_errors += len(errors)
            all_errors[str(filepath)] = errors
        else:
            passed_files += 1

    # Print errors
    if all_errors:
        print("\n" + "=" * 60)
        print("VALIDATION ERRORS")
        print("=" * 60)
        for filepath_str, errs in all_errors.items():
            print(f"\n  {filepath_str}:")
            for err in errs:
                print(f"    - {err}")

    # Print summary
    print("\n" + "-" * 40)
    print("SUMMARY")
    print("-" * 40)
    print(f"  Files checked:  {total_files}")
    print(f"  Passed:         {passed_files}")
    print(f"  Failed:         {failed_files}")
    print(f"  Total errors:   {total_errors}")

    return 0 if failed_files == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
