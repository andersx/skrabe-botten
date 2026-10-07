"""Load papers + curations for Discord posting from existing out/ files."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any


def _day_of(paper: dict[str, Any]) -> str:
    return (paper.get("published") or paper.get("announce_date") or paper.get("updated") or "")[:10]


def _iter_dates(start: date, end: date) -> list[date]:
    days: list[date] = []
    d = start
    while d <= end:
        days.append(d)
        d += timedelta(days=1)
    return days


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _prefer_curated_paths(curated_dir: Path) -> list[Path]:
    """Prefer .flash range files, then plain range, then daily."""
    files = list(curated_dir.glob("*.json"))
    files = [f for f in files if "comparison" not in f.stem]

    def rank(p: Path) -> tuple[int, str]:
        stem = p.stem
        if stem.endswith(".flash"):
            return (0, stem)
        if "_to_" in stem and not stem.endswith(".pro"):
            return (1, stem)
        if "_to_" in stem:
            return (2, stem)
        return (3, stem)

    return sorted(files, key=rank)


def build_curation_index(out_dir: Path) -> dict[str, dict[str, Any]]:
    """Map paper id -> curation, preferring flash over pro/default when both exist."""
    curated_dir = out_dir / "curated"
    index: dict[str, dict[str, Any]] = {}
    # Load lower-priority first so higher-priority overwrites
    for path in reversed(_prefer_curated_paths(curated_dir)):
        payload = _load_json(path)
        for c in payload.get("curations") or []:
            pid = c.get("id")
            if pid:
                index[pid] = c
    return index


def build_paper_index(out_dir: Path) -> dict[str, dict[str, Any]]:
    """Map paper id -> paper metadata from all raw scrapes."""
    raw_dir = out_dir / "raw"
    index: dict[str, dict[str, Any]] = {}
    for path in sorted(raw_dir.glob("*.json")):
        payload = _load_json(path)
        for p in payload.get("papers") or []:
            pid = p.get("id")
            if pid:
                index[pid] = p
    return index


def usage_from_curated(out_dir: Path) -> dict[str, Any]:
    curated_dir = out_dir / "curated"
    for path in _prefer_curated_paths(curated_dir):
        usage = _load_json(path).get("usage")
        if usage:
            return usage
    return {"model": "deepseek-flash", "estimated_usd": 0.0}


def load_day_from_store(
    out_dir: Path,
    day: date | str,
    *,
    papers_by_id: dict[str, dict[str, Any]] | None = None,
    curations_by_id: dict[str, dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Assemble papers + curations for one calendar day from existing files."""
    day_s = day.isoformat() if isinstance(day, date) else day

    # Prefer dedicated daily files when present and non-empty
    daily_raw = out_dir / "raw" / f"{day_s}.json"
    daily_cur = out_dir / "curated" / f"{day_s}.json"
    if daily_raw.exists() and daily_cur.exists():
        raw = _load_json(daily_raw)
        cur = _load_json(daily_cur)
        papers = raw.get("papers") or []
        if papers:
            return papers, cur.get("curations") or [], cur.get("usage") or usage_from_curated(out_dir)

    papers_by_id = papers_by_id or build_paper_index(out_dir)
    curations_by_id = curations_by_id or build_curation_index(out_dir)

    papers = [p for p in papers_by_id.values() if _day_of(p) == day_s]
    papers.sort(key=lambda p: p.get("id", ""))
    curations: list[dict[str, Any]] = []
    for p in papers:
        c = curations_by_id.get(p["id"])
        if c is None:
            curations.append(
                {
                    "id": p["id"],
                    "keep": False,
                    "categories": [],
                    "priority": 3,
                    "takeaway": "",
                }
            )
        else:
            curations.append(c)
    return papers, curations, usage_from_curated(out_dir)


def load_range_days(
    out_dir: Path, start: date, end: date
) -> list[tuple[str, list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]]:
    papers_by_id = build_paper_index(out_dir)
    curations_by_id = build_curation_index(out_dir)
    rows = []
    for d in _iter_dates(start, end):
        papers, curations, usage = load_day_from_store(
            out_dir, d, papers_by_id=papers_by_id, curations_by_id=curations_by_id
        )
        rows.append((d.isoformat(), papers, curations, usage))
    return rows
