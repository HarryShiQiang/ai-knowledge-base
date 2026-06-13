#!/usr/bin/env python3
"""Score knowledge article JSON files across 5 quality dimensions.

Outputs per-file dimension scores, letter grades, and a visual progress bar.
Exits 0 if no C-grade files, 1 otherwise.

Usage:
    python hooks/check_quality.py <json_file> [json_file2 ...]
    python hooks/check_quality.py knowledge/articles/*.json
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SUMMARY_MIN_LENGTH = 20
SUMMARY_MAX_POINTS_LENGTH = 50

SCORE_FIELD_MIN = 1
SCORE_FIELD_MAX = 10

TAG_COUNT_MIN = 1
TAG_COUNT_BEST_MAX = 3

FORMAT_FIELDS = ("id", "title", "source_url", "status")
FORMAT_PER_FIELD_PTS = 4
FORMAT_TIMESTAMP_PTS = 4
FORMAT_TOTAL_PTS = (len(FORMAT_FIELDS) + 1) * FORMAT_PER_FIELD_PTS  # 20

TECH_KEYWORDS: list[str] = [
    "llm", "agent", "rag", "fine-tuning", "prompt", "transformer",
    "multimodal", "embedding", "token", "context", "inference",
    "training", "benchmark", "deploy", "open-source",
]

CN_BUZZWORDS: tuple[str, ...] = (
    "赋能", "抓手", "闭环", "打通", "全链路", "底层逻辑",
    "颗粒度", "对齐", "拉通", "沉淀", "强大的", "革命性的",
)

EN_BUZZWORDS: tuple[str, ...] = (
    "groundbreaking", "revolutionary", "game-changing",
    "cutting-edge", "disruptive", "best-in-class",
    "unprecedented", "world-class", "state-of-the-art",
    "next-generation", "paradigm-shift",
)

STANDARD_TAGS: frozenset[str] = frozenset({
    "llm", "agent", "rag", "multimodal", "fine-tuning",
    "prompt-engineering", "inference", "training", "embeddings",
    "vector-db", "function-calling", "tool-use", "open-source",
    "deployment", "evaluation", "safety", "alignment", "benchmark",
    "code-generation", "chatbot", "search", "document-processing",
    "autonomous-agent", "token-optimization", "deep-research",
    "nlp", "computer-vision", "speech", "text-to-speech",
    "orchestration", "multi-agent",
})

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class DimensionScore:
    """Score for a single quality dimension."""

    label: str
    max_points: int
    earned: int
    detail: str = ""


@dataclass
class QualityReport:
    """Complete quality report for one article."""

    filepath: str
    dimensions: list[DimensionScore] = field(default_factory=list)
    total: int = 0
    grade: str = "C"
    max_total: int = 100

    @property
    def percentage(self) -> float:
        return (self.total / self.max_total * 100) if self.max_total > 0 else 0.0


# ---------------------------------------------------------------------------
# Dimension scorers
# ---------------------------------------------------------------------------


def _score_summary_quality(data: dict[str, Any]) -> DimensionScore:
    """Score summary quality. Max 25 points."""
    earned = 0
    detail_parts: list[str] = []

    summary = data.get("summary", "")
    if not isinstance(summary, str):
        return DimensionScore("摘要质量", 25, 0, "summary 字段缺失或非字符串")

    length = len(summary)

    if length >= SUMMARY_MAX_POINTS_LENGTH:
        earned += 25
        detail_parts.append(f"长度达标 ({length} 字，满分)")
    elif length >= SUMMARY_MIN_LENGTH:
        earned += 12
        detail_parts.append(f"长度基本达标 ({length} 字，12/25)")
    else:
        detail_parts.append(f"长度不足 ({length} 字，0/25)")

    if earned > 0:
        lower = summary.lower()
        matched = [kw for kw in TECH_KEYWORDS if kw in lower]
        if matched:
            bonus = min(len(matched), 5)
            earned += bonus
            # bonus but capped at 25
            if earned > 25:
                earned = 25
            detail_parts.append(f"技术关键词奖励 +{bonus}: {', '.join(matched[:5])}")

    earned = min(earned, 25)
    return DimensionScore("摘要质量", 25, earned, "; ".join(detail_parts))


def _score_tech_depth(data: dict[str, Any]) -> DimensionScore:
    """Score technical depth based on the score field. Max 25 points."""
    score = data.get("score")
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        return DimensionScore("技术深度", 25, 0, "score 字段缺失或非数字")

    if score < SCORE_FIELD_MIN or score > SCORE_FIELD_MAX:
        return DimensionScore(
            "技术深度", 25, 0, f"score 值 {score} 超出 1-10 范围"
        )

    mapped = int((score / SCORE_FIELD_MAX) * 25)
    return DimensionScore(
        "技术深度", 25, mapped, f"score={score} → 映射 {mapped}/25"
    )


def _score_format(data: dict[str, Any]) -> DimensionScore:
    """Score format compliance. Max 20 points."""
    earned = 0
    detail_parts: list[str] = []

    for field in FORMAT_FIELDS:
        value = data.get(field)
        if value is not None and isinstance(value, str) and len(value) > 0:
            earned += FORMAT_PER_FIELD_PTS
        else:
            detail_parts.append(f"缺少 {field}")

    has_ts = False
    for ts_key in ("published_at", "fetched_at"):
        ts = data.get(ts_key)
        if isinstance(ts, str) and len(ts) > 0:
            has_ts = True
            break
    if has_ts:
        earned += FORMAT_TIMESTAMP_PTS
    else:
        detail_parts.append("缺少时间戳 (published_at / fetched_at)")

    return DimensionScore("格式规范", FORMAT_TOTAL_PTS, earned, "; ".join(detail_parts) or "全部字段完整")


def _score_tags(data: dict[str, Any]) -> DimensionScore:
    """Score tag precision. Max 15 points."""
    earned = 0
    detail_parts: list[str] = []

    tags = data.get("tags")
    if not isinstance(tags, list):
        return DimensionScore("标签精度", 15, 0, "tags 字段缺失或非列表")

    tag_count = len(tags)
    if tag_count == 0:
        return DimensionScore("标签精度", 15, 0, "标签列表为空")

    standard_hits = sum(1 for t in tags if isinstance(t, str) and t.lower() in STANDARD_TAGS)
    nonstandard = tag_count - standard_hits

    if 1 <= tag_count <= TAG_COUNT_BEST_MAX:
        base = 10
        detail_parts.append(f"标签数量优秀 ({tag_count} 个, 10/15)")
    elif tag_count <= 6:
        base = 7
        detail_parts.append(f"标签数量适中 ({tag_count} 个, 7/15)")
    else:
        base = 4
        detail_parts.append(f"标签过多 ({tag_count} 个, 4/15)")

    bonus = min(standard_hits, 5)
    earned = base + bonus
    if earned > 15:
        earned = 15
    if standard_hits > 0:
        detail_parts.append(f"标准标签命中 +{bonus}: {standard_hits}/{tag_count}")
    if nonstandard > 0:
        detail_parts.append(f"非标准标签: {nonstandard} 个")

    return DimensionScore("标签精度", 15, earned, "; ".join(detail_parts))


def _has_buzzwords(text: str) -> list[str]:
    """Collect all buzzwords found in text (case-insensitive for English)."""
    found: list[str] = []
    lower = text.lower()

    for word in CN_BUZZWORDS:
        if word in text:
            found.append(word)

    for word in EN_BUZZWORDS:
        if word in lower:
            found.append(word)

    return found


def _score_buzzwords(data: dict[str, Any]) -> DimensionScore:
    """Score buzzword absence. Max 15 points."""
    fields_to_check = ["summary", "chinese_summary", "title"]
    all_found: list[str] = []

    for field in fields_to_check:
        value = data.get(field, "")
        if isinstance(value, str):
            all_found.extend(_has_buzzwords(value))

    all_found = sorted(set(all_found))
    penalty = len(all_found) * 3
    if penalty > 15:
        penalty = 15
    earned = 15 - penalty

    if all_found:
        return DimensionScore(
            "空洞词检测", 15, earned,
            f"检测到 {len(all_found)} 个: {', '.join(all_found)} (扣 {penalty} 分)"
        )
    return DimensionScore("空洞词检测", 15, 15, "未检测到空洞词")


# ---------------------------------------------------------------------------
# Progress bar & grading
# ---------------------------------------------------------------------------


def _grade(percentage: float) -> str:
    """Map percentage to letter grade."""
    if percentage >= 80:
        return "A"
    if percentage >= 60:
        return "B"
    return "C"


def _progress_bar(percentage: float, width: int = 30) -> str:
    """Render an ASCII progress bar."""
    filled = int(width * percentage / 100)
    bar = "#" * filled + "-" * (width - filled)
    return f"[{bar}] {percentage:5.1f}%"


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


def _expand_paths(raw_args: list[str]) -> list[Path]:
    """Expand glob patterns and collect unique .json file paths."""
    seen: set[Path] = set()
    result: list[Path] = []

    for arg in raw_args:
        path = Path(arg)
        if "*" in arg or "?" in arg or "[" in arg:
            parent = path.parent if path.parent != Path(".") else Path()
            if parent != Path(".") and parent.exists():
                matches = sorted(parent.glob(path.name))
            else:
                matches = sorted(Path().glob(arg))
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


def _read_json(filepath: Path) -> dict[str, Any] | None:
    """Read and parse a JSON file. Returns None on failure."""
    try:
        return json.loads(filepath.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def score_file(filepath: Path) -> QualityReport:
    """Score a single JSON file across all dimensions."""
    data = _read_json(filepath)
    if data is None or not isinstance(data, dict):
        dim = DimensionScore("文件读取", 0, 0, "无法读取或解析 JSON")
        report = QualityReport(
            filepath=str(filepath),
            dimensions=[dim],
            total=0,
            grade="C",
        )
        return report

    dimensions = [
        _score_summary_quality(data),
        _score_tech_depth(data),
        _score_format(data),
        _score_tags(data),
        _score_buzzwords(data),
    ]

    total = sum(d.earned for d in dimensions)
    grade = _grade(total)

    return QualityReport(
        filepath=str(filepath),
        dimensions=dimensions,
        total=total,
        grade=grade,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    """Entry point. Returns 0 if all files grade A or B, 1 if any C."""
    if len(sys.argv) < 2:
        print(
            "Usage: python hooks/check_quality.py <json_file> [json_file2 ...]",
            file=sys.stderr,
        )
        return 1

    raw_args = sys.argv[1:]
    filepaths = _expand_paths(raw_args)

    if not filepaths:
        print(
            f"Error: no matching JSON files found for: {' '.join(raw_args)}",
            file=sys.stderr,
        )
        return 1

    total_files = len(filepaths)
    reports: list[QualityReport] = []
    has_c_grade = False
    grades: dict[str, int] = {"A": 0, "B": 0, "C": 0}

    print(f"Scoring {total_files} file(s)...\n")

    for idx, filepath in enumerate(filepaths, start=1):
        report = score_file(filepath)
        reports.append(report)
        grades[report.grade] += 1

        if report.grade == "C":
            has_c_grade = True

        display_name = filepath.name if len(f"{filepath.name}") > 0 else str(filepath)
        bar = _progress_bar(report.percentage)
        print(f"  [{idx:>{len(str(total_files))}}/{total_files}] {bar}  {report.grade}  {display_name}")

    # Detail output
    print("\n" + "=" * 64)
    print("DETAIL BY FILE")
    print("=" * 64)

    for report in reports:
        print(f"\n-- {report.filepath}  --  Total {report.total}/100"
              f"  {_progress_bar(report.percentage)}  {report.grade}")
        for dim in report.dimensions:
            print(f"   {dim.label:8s}  {dim.earned:>2}/{dim.max_points:<3}"
                  f"  {dim.detail}")

    # Summary
    print("\n" + "=" * 64)
    print("SUMMARY")
    print("=" * 64)
    print(f"  Files scored:  {total_files}")
    print(f"  A (>=80):      {grades['A']}")
    print(f"  B (>=60):      {grades['B']}")
    print(f"  C (<60):       {grades['C']}")

    if has_c_grade:
        print(f"\n  ⚠  {grades['C']} file(s) scored below 60 — quality review required.")

    return 1 if has_c_grade else 0


if __name__ == "__main__":
    sys.exit(main())
