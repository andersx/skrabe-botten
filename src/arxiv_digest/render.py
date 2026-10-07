from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


def _label(day: date | str) -> str:
    return day.isoformat() if isinstance(day, date) else day


def write_raw(
    out_dir: Path, day: date | str, papers: list[dict[str, Any]], meta: dict[str, Any]
) -> Path:
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    label = _label(day)
    path = raw_dir / f"{label}.json"
    payload = {
        "day_utc": label,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(papers),
        "meta": meta,
        "papers": papers,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def write_full_digest(
    out_dir: Path, day: date | str, papers: list[dict[str, Any]], categories: list
) -> Path:
    digest_dir = out_dir / "digest"
    digest_dir.mkdir(parents=True, exist_ok=True)
    label = _label(day)
    path = digest_dir / f"{label}.md"

    cat_labels = []
    for c in categories:
        if isinstance(c, dict):
            name = c.get("name") or c.get("id") or "category"
            cid = c.get("id")
            cat_labels.append(f"{name} (`{cid}`)" if cid else str(name))
        else:
            cat_labels.append(f"`{c}`")

    lines = [
        f"# arXiv Digest — {label}",
        "",
        f"**Papers:** {len(papers)}",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "Categories: " + ", ".join(cat_labels) + ".",
        "",
        "Source: arXiv RSS announcement day (same as website /recent), or Atom submittedDate range.",
        "",
    ]
    for i, p in enumerate(papers, 1):
        authors = ", ".join(p.get("authors", [])[:8])
        if len(p.get("authors", [])) > 8:
            authors += f", et al. ({len(p['authors'])} authors)"
        lines.extend(
            [
                f"### {i}. {p['title']}",
                "",
                f"- **arXiv:** [{p['id']}]({p['url']})",
                f"- **Submitted:** {p.get('published', '')}",
                f"- **Primary category:** `{p.get('primary_category', '')}`",
                f"- **Categories:** " + ", ".join(f"`{c}`" for c in p.get("categories", [])),
                f"- **Authors:** {authors}",
                "",
                f"**Abstract.** {p.get('abstract', '')}",
                "",
                "---",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_curated(
    out_dir: Path,
    day: date | str,
    papers: list[dict[str, Any]],
    curations: list[dict[str, Any]],
    section_order: list[dict[str, Any]],
    usage: dict[str, Any],
) -> Path:
    curated_dir = out_dir / "curated"
    curated_dir.mkdir(parents=True, exist_ok=True)
    label = _label(day)
    path = curated_dir / f"{label}.md"

    by_id = {p["id"]: p for p in papers}
    kept = [c for c in curations if c.get("keep")]
    kept.sort(key=lambda c: (c.get("priority", 3), by_id.get(c["id"], {}).get("title", "")))

    lines = [
        f"# Curated arXiv — Pharma Computational Chemistry — {label}",
        "",
        f"**Kept:** {len(kept)} / {len(papers)} papers",
        f"**Model:** `{usage.get('model', '')}`",
        f"**Tokens:** in={usage.get('prompt_tokens', 0)}, out={usage.get('completion_tokens', 0)}",
        f"**Est. cost:** ${usage.get('estimated_usd', 0):.4f}",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]

    used_ids: set[str] = set()
    for section in section_order:
        labels = set(section.get("labels", []))
        section_papers = [
            c
            for c in kept
            if c["id"] not in used_ids and labels.intersection(c.get("categories") or [])
        ]
        if not section_papers:
            continue
        lines.append(f"## {section['title']}")
        lines.append("")
        for c in section_papers:
            p = by_id[c["id"]]
            used_ids.add(c["id"])
            tags = ", ".join(f"`{t}`" for t in c.get("categories", []))
            lines.extend(
                [
                    f"### P{c.get('priority', 3)} — {p['title']}",
                    "",
                    f"**Takeaway:** {c.get('takeaway', '')}",
                    "",
                    f"- [{p['id']}]({p['url']}) · submitted {p.get('published', '')} · `{p.get('primary_category', '')}`",
                    f"- **Tags:** {tags}",
                    f"- **Authors:** " + ", ".join(p.get("authors", [])[:6])
                    + (f", et al." if len(p.get("authors", [])) > 6 else ""),
                    "",
                    f"**Abstract.** {p.get('abstract', '')}",
                    "",
                    "---",
                    "",
                ]
            )

    leftovers = [c for c in kept if c["id"] not in used_ids]
    if leftovers:
        lines.append("## Uncategorized keeps")
        lines.append("")
        for c in leftovers:
            p = by_id[c["id"]]
            lines.append(f"- **{p['title']}** — [{p['id']}]({p['url']}) — {c.get('takeaway', '')}")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_curation_json(
    out_dir: Path, day: date | str, curations: list[dict[str, Any]], usage: dict[str, Any]
) -> Path:
    curated_dir = out_dir / "curated"
    curated_dir.mkdir(parents=True, exist_ok=True)
    label = _label(day)
    path = curated_dir / f"{label}.json"
    path.write_text(
        json.dumps({"day_utc": label, "usage": usage, "curations": curations}, indent=2),
        encoding="utf-8",
    )
    return path
