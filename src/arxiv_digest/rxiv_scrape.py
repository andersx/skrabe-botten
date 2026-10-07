"""bioRxiv / medRxiv scraping via the public Cold Spring Harbor API."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import date, timedelta
from typing import Any


API_BASE = "https://api.biorxiv.org/details"

DEFAULT_BIORXIV_CATEGORIES = [
    "bioinformatics",
    "biophysics",
    "systems biology",
    "bioengineering",
    "biochemistry",
    "synthetic biology",
    "genomics",
]

DEFAULT_MEDRXIV_CATEGORIES = [
    "health informatics",
    "genetic and genomic medicine",
    "pharmacology and therapeutics",
    "radiology and imaging",
]

# Extra keyword screen (title+abstract) for computational / drug-discovery relevance
DEFAULT_KEYWORDS = [
    "docking",
    "virtual screening",
    "molecular dynamics",
    "machine learning",
    "deep learning",
    "neural network",
    "protein structure",
    "alphafold",
    "cryo-em",
    "binding affinity",
    "drug discovery",
    "drug design",
    "ligand",
    "qsar",
    "admet",
    "cheminformatic",
    "force field",
    "free energy",
    "generative",
    "graph neural",
    "structure prediction",
    "molecular docking",
    "pharmacophore",
    "computational",
    "in silico",
]


def _urlopen_json(url: str, *, timeout: int = 90, max_retries: int = 5) -> dict[str, Any]:
    delay = 2.0
    last_err: Exception | None = None
    for attempt in range(max_retries):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "arxiv-digest/1.0 (research)",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_err = exc
            if exc.code not in (429, 500, 502, 503, 504):
                raise
            time.sleep(delay)
            delay = min(delay * 2, 60.0)
        except urllib.error.URLError as exc:
            last_err = exc
            time.sleep(delay)
            delay = min(delay * 2, 60.0)
    assert last_err is not None
    raise last_err


def _keyword_match(title: str, abstract: str, keywords: list[str]) -> bool:
    blob = f"{title} {abstract}".lower()
    return any(k.lower() in blob for k in keywords)


def _normalize_item(item: dict[str, Any], server: str) -> dict[str, Any]:
    doi = (item.get("doi") or "").strip()
    title = (item.get("title") or "").strip()
    abstract = (item.get("abstract") or "").strip()
    date_s = (item.get("date") or "")[:10]
    category = (item.get("category") or "").strip()
    authors_raw = item.get("authors") or ""
    if isinstance(authors_raw, str):
        authors = [a.strip() for a in authors_raw.split(";") if a.strip()]
    else:
        authors = list(authors_raw)
    version = item.get("version") or "1"
    if server == "biorxiv":
        url = f"https://www.biorxiv.org/content/{doi}v{version}"
        pid = f"biorxiv:{doi}"
    else:
        url = f"https://www.medrxiv.org/content/{doi}v{version}"
        pid = f"medrxiv:{doi}"
    return {
        "id": pid,
        "url": url,
        "title": title,
        "abstract": abstract,
        "authors": authors,
        "published": date_s,
        "announce_date": date_s,
        "announce_type": server,
        "primary_category": category or server,
        "categories": [category] if category else [server],
        "source": server,
        "doi": doi,
        "version": str(version),
        "matched_query_categories": [category or server],
    }


def fetch_rxiv_day(
    server: str,
    day: date,
    *,
    categories: list[str] | None = None,
    keywords: list[str] | None = None,
    keyword_filter: bool = True,
    pause: float = 0.35,
) -> list[dict[str, Any]]:
    """Fetch one calendar day from bioRxiv or medRxiv, optionally filtered."""
    day_s = day.isoformat()
    categories_l = {c.lower() for c in (categories or [])}
    keywords = list(keywords or DEFAULT_KEYWORDS)
    papers: list[dict[str, Any]] = []
    cursor = 0
    total: int | None = None

    while True:
        url = f"{API_BASE}/{server}/{day_s}/{day_s}/{cursor}"
        data = _urlopen_json(url)
        messages = data.get("messages") or []
        status = (messages[0].get("status") if messages else "") or ""
        if status.startswith("no articles") or status not in ("ok",):
            break
        collection = data.get("collection") or []
        if not collection:
            break
        if total is None and messages:
            try:
                total = int(messages[0].get("total") or 0)
            except (TypeError, ValueError):
                total = 0
        for item in collection:
            paper = _normalize_item(item, server)
            cat = (paper.get("primary_category") or "").lower()
            if categories_l and cat not in categories_l:
                continue
            if keyword_filter and keywords and not _keyword_match(
                paper.get("title", ""), paper.get("abstract", ""), keywords
            ):
                continue
            papers.append(paper)
        cursor += len(collection)
        if total is not None and cursor >= total:
            break
        time.sleep(pause)

    print(
        f"  {server} {day_s}: kept {len(papers)}"
        + (f" (from cursor total {total})" if total is not None else ""),
        flush=True,
    )
    return papers


def scrape_rxiv_range(
    cfg: dict[str, Any],
    server: str,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    """Scrape bioRxiv or medRxiv over an inclusive date range (day-by-day)."""
    key = "biorxiv" if server == "biorxiv" else "medrxiv"
    section = cfg.get(key) or {}
    if not section.get("enabled", True):
        return []

    if server == "biorxiv":
        default_cats = DEFAULT_BIORXIV_CATEGORIES
    else:
        default_cats = DEFAULT_MEDRXIV_CATEGORIES

    categories = [
        c["name"] if isinstance(c, dict) else c
        for c in (section.get("categories") or default_cats)
    ]
    keywords = list(section.get("relevance_keywords") or DEFAULT_KEYWORDS)
    keyword_filter = bool(section.get("keyword_filter", True))

    print(f"Fetching {server} {start.isoformat()} → {end.isoformat()}…", flush=True)
    by_id: dict[str, dict[str, Any]] = {}
    d = start
    while d <= end:
        for p in fetch_rxiv_day(
            server,
            d,
            categories=categories,
            keywords=keywords,
            keyword_filter=keyword_filter,
        ):
            # Prefer newer version if same DOI reappears
            prev = by_id.get(p["id"])
            if prev is None or int(p.get("version") or 1) >= int(prev.get("version") or 1):
                by_id[p["id"]] = p
        d += timedelta(days=1)
        time.sleep(0.2)

    papers = sorted(by_id.values(), key=lambda p: (p.get("published", ""), p["id"]), reverse=True)
    print(f"  {server} range unique: {len(papers)}", flush=True)
    return papers
