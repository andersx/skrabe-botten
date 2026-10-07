"""Format curated digests for Discord (plain text / markdown, no embeds)."""

from __future__ import annotations

from datetime import date
from typing import Any

CORE_LABELS = {
    "sbdd",
    "virtual_screening",
    "ligand_based_vs",
    "property_prediction",
    "protein_structure",
    "molecular_simulation",
    "binding_affinity",
}

SOFT_ONLY_LABELS = {
    "property_prediction",
}

DISCORD_CONTENT_LIMIT = 2000
TAKEAWAY_LIMIT = 220


def core_categories(curation: dict[str, Any]) -> set[str]:
    return set(curation.get("categories") or []) & CORE_LABELS


def passes_core_filter(curation: dict[str, Any]) -> bool:
    """Keep if it has a hard core tag; drop other_pharma_ml-only and property_prediction-only."""
    if not curation.get("keep"):
        return False
    cats = core_categories(curation)
    if not cats:
        return False
    if cats <= SOFT_ONLY_LABELS:
        return False
    return True


def _short_id(arxiv_id: str) -> str:
    return arxiv_id.split("v")[0]


def _source_tag(paper: dict[str, Any]) -> str:
    src = (paper.get("source") or "").lower()
    pid = str(paper.get("id", ""))
    if src == "chemrxiv" or pid.startswith("chemrxiv:"):
        return "[ChemRxiv]"
    if src == "biorxiv" or pid.startswith("biorxiv:"):
        return "[bioRxiv]"
    if src == "medrxiv" or pid.startswith("medrxiv:"):
        return "[medRxiv]"
    return "[arXiv]"


def _truncate(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0] + "…"


def _pretty_day(day: date | str) -> str:
    if isinstance(day, date):
        d = day
    else:
        try:
            d = date.fromisoformat(str(day)[:10])
        except ValueError:
            return str(day)
    return d.strftime("%-d %B %Y")


def _assign_sections(
    curations: list[dict[str, Any]],
    section_order: list[dict[str, Any]],
    papers_by_id: dict[str, dict[str, Any]] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """One section per paper (first matching section in config order).

    Caller is responsible for filtering which curations to include.
    """
    core_sections = [s for s in section_order if s.get("id") != "other_pharma_ml"]
    used: set[str] = set()
    ordered: list[tuple[str, dict[str, Any]]] = []
    papers_by_id = papers_by_id or {}

    priorities = sorted({int(c.get("priority", 3)) for c in curations})
    for pri in priorities:
        pool = [c for c in curations if int(c.get("priority", 3)) == pri]
        for sec in core_sections:
            sec_labels = set(sec.get("labels") or [])
            block = [
                c
                for c in pool
                if c["id"] not in used and sec_labels.intersection(c.get("categories") or [])
            ]
            block.sort(
                key=lambda c: papers_by_id.get(c["id"], {}).get("title", c["id"]).lower()
            )
            for c in block:
                used.add(c["id"])
                ordered.append((sec["title"], c))
        for c in pool:
            if c["id"] not in used:
                used.add(c["id"])
                ordered.append(("Other", c))
    return ordered


def _group_by_section(
    assigned: list[tuple[str, dict[str, Any]]],
) -> list[tuple[str, list[dict[str, Any]]]]:
    merged: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for sec, c in assigned:
        if sec not in merged:
            order.append(sec)
            merged[sec] = []
        merged[sec].append(c)
    return [(sec, merged[sec]) for sec in order]


def _pack_messages(header: str, blocks: list[str]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    current = header
    for block in blocks:
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= DISCORD_CONTENT_LIMIT:
            current = candidate
            continue
        # Don't send a header-only stub — carry the header into the next chunk
        if current and current != header:
            messages.append({"content": current, "embeds": []})
            current = ""
        prefixed = f"{header}\n\n{block}" if header else block
        if len(prefixed) <= DISCORD_CONTENT_LIMIT:
            current = prefixed
            continue
        sec_title = block.split("\n", 1)[0]
        chunk = f"{header}\n\n{sec_title}" if header else sec_title
        for line_group in block.split("\n\n")[1:]:
            piece = f"{chunk}\n\n{line_group}" if chunk else line_group
            if len(piece) <= DISCORD_CONTENT_LIMIT:
                chunk = piece
            else:
                if chunk:
                    messages.append({"content": chunk, "embeds": []})
                chunk = f"{sec_title}\n\n{line_group}"
                if len(chunk) > DISCORD_CONTENT_LIMIT:
                    chunk = _truncate(chunk, DISCORD_CONTENT_LIMIT - 1)
        current = chunk
        header = ""  # only prefix once
    if current:
        messages.append({"content": current, "embeds": []})
    return messages


def format_daily_discord(
    day: date | str,
    papers: list[dict[str, Any]],
    curations: list[dict[str, Any]],
    usage: dict[str, Any],
    section_order: list[dict[str, Any]],
    *,
    max_priority: int = 2,
    include_p3: bool = False,
    core_filter: bool = True,
    header: str | None = None,
) -> list[dict[str, Any]]:
    """
    Flat Discord messages (no embeds):

        **5 October 2026**

        **Virtual screening**
        🔥 [ChemRxiv] [Title](<url>)
        Takeaway…
        [arXiv] [Title](<url>)
        Takeaway…
    """
    by_id = {p["id"]: p for p in papers}
    kept = [c for c in curations if c.get("keep")]
    if not include_p3:
        kept = [c for c in kept if c.get("priority") in (1, 2)]
    kept = [c for c in kept if int(c.get("priority", 3)) <= max_priority]
    if core_filter:
        kept = [c for c in kept if passes_core_filter(c)]

    day_header = header or f"**{_pretty_day(day)}**"

    if not kept:
        return [{"content": f"{day_header}\n\n_No picks to post._", "embeds": []}]

    assigned = _assign_sections(kept, section_order, by_id)
    grouped = _group_by_section(assigned)

    blocks: list[str] = []
    for sec, items in grouped:
        lines = [f"**{sec}**"]
        for c in items:
            p = by_id[c["id"]]
            url = p.get("url") or f"https://arxiv.org/abs/{p['id']}"
            title = p.get("title", _short_id(p["id"]))
            takeaway = _truncate(c.get("takeaway", ""), TAKEAWAY_LIMIT)
            src = _source_tag(p)
            prefix = "🔥 " if int(c.get("priority", 2)) == 1 else ""
            lines.append(f"{prefix}{src} [{title}](<{url}>)")
            lines.append(takeaway)
            lines.append("")
        blocks.append("\n".join(lines).rstrip())

    return _pack_messages(day_header, blocks)


def format_daily_discord_plain(
    day: date | str,
    papers: list[dict[str, Any]],
    curations: list[dict[str, Any]],
    usage: dict[str, Any],
    section_order: list[dict[str, Any]],
) -> str:
    """Single concatenated plain preview (may exceed Discord limit)."""
    msgs = format_daily_discord(day, papers, curations, usage, section_order)
    return "\n\n---\n\n".join(m["content"] for m in msgs)
