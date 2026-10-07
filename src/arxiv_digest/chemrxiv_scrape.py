"""ChemRxiv scraping via Crossref (OpenEngage when reachable)."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from html import unescape
from typing import Any


CHEMRXIV_PREFIX = "10.26434"
OPENENGAGE_BASE = "https://chemrxiv.org/engage/chemrxiv/public-api/v1"
CROSSREF_WORKS = "https://api.crossref.org/works"

# ChemRxiv subject areas closest to pharma computational chemistry.
# OpenEngage exposes these by name; Crossref does not, so we approximate
# with relevance_keywords when falling back.
DEFAULT_CATEGORY_NAMES = [
    "Theoretical and Computational Chemistry",
    "Biological and Medicinal Chemistry",
]

DEFAULT_KEYWORDS = [
    "docking",
    "virtual screening",
    "drug discovery",
    "drug design",
    "medicinal",
    "pharmaceutical",
    "ligand",
    "binding affinity",
    "protein–ligand",
    "protein-ligand",
    "qsar",
    "admet",
    "pharmacokinet",
    "molecular dynamics",
    "free energy",
    "mm/pbsa",
    "mmgbsa",
    "force field",
    "machine learning",
    "deep learning",
    "neural network",
    "graph neural",
    "cheminformatic",
    "retrosynthe",
    "generative chem",
    "molecule generation",
    "conformational",
    "cryo-em",
    "crystal structure prediction",
    "pharmacophore",
    "fragment-based",
    "structure-based",
    "hit discovery",
    "lead optim",
    "kinase inhibitor",
    "antibody",
    "peptide drug",
    "interatomic potential",
    "mlip",
    "quantum chemistry",
    "density functional",
    "chemical biology",
    "enzyme inhibit",
]


def _strip_jats(abstract: str) -> str:
    text = unescape(abstract or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _urlopen_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: int = 60,
    max_retries: int = 6,
) -> dict[str, Any]:
    hdrs = {
        "User-Agent": "arxiv-digest/1.0 (research; mailto:local)",
        "Accept": "application/json",
        **(headers or {}),
    }
    delay = 2.0
    last_err: Exception | None = None
    for attempt in range(max_retries):
        req = urllib.request.Request(url, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            last_err = exc
            if exc.code not in (429, 500, 502, 503, 504):
                raise
            wait = delay
            ra = exc.headers.get("Retry-After") if exc.headers else None
            if ra:
                try:
                    wait = max(wait, float(ra))
                except ValueError:
                    pass
            print(f"  ChemRxiv/Crossref HTTP {exc.code}; sleep {wait:.0f}s…", flush=True)
            time.sleep(wait)
            delay = min(delay * 2, 60.0)
        except urllib.error.URLError as exc:
            last_err = exc
            time.sleep(delay)
            delay = min(delay * 2, 60.0)
    assert last_err is not None
    raise last_err


def try_openengage_categories(
    user_agent: str = "Mozilla/5.0 arxiv-digest/1.0",
) -> list[dict[str, Any]] | None:
    """Return OpenEngage categories, or None if Cloudflare/API blocks us."""
    url = f"{OPENENGAGE_BASE}/categories"
    headers = {
        "User-Agent": user_agent,
        "Origin": "https://chemrxiv.org",
        "Referer": "https://chemrxiv.org/",
    }
    try:
        data = _urlopen_json(url, headers=headers, max_retries=2)
    except Exception as exc:  # noqa: BLE001
        print(f"  ChemRxiv OpenEngage unavailable ({exc}); using Crossref.", flush=True)
        return None
    return data.get("categories") or []


def _match_category_ids(
    available: list[dict[str, Any]], wanted_names: list[str]
) -> list[tuple[str, str]]:
    wanted_l = {n.lower(): n for n in wanted_names}
    matched: list[tuple[str, str]] = []
    for cat in available:
        name = (cat.get("name") or "").strip()
        cid = cat.get("id") or ""
        if name.lower() in wanted_l and cid:
            matched.append((cid, name))
    return matched


def _normalize_openengage_item(item: dict[str, Any], matched_cat: str) -> dict[str, Any]:
    doi = (item.get("doi") or "").strip()
    pid = f"chemrxiv:{doi}" if doi else f"chemrxiv:{item.get('id', '')}"
    authors = []
    for a in item.get("authors") or []:
        if isinstance(a, dict):
            name = a.get("name") or " ".join(
                x for x in [a.get("firstName"), a.get("lastName")] if x
            )
            if name:
                authors.append(name.strip())
        elif isinstance(a, str):
            authors.append(a)
    cats = []
    for c in item.get("categories") or []:
        if isinstance(c, dict) and c.get("name"):
            cats.append(c["name"])
        elif isinstance(c, str):
            cats.append(c)
    if matched_cat and matched_cat not in cats:
        cats = [matched_cat] + cats
    published = (item.get("publishedDate") or item.get("published_date") or "")[:10]
    url = item.get("url") or (f"https://doi.org/{doi}" if doi else "")
    return {
        "id": pid,
        "url": url,
        "title": (item.get("title") or "").strip(),
        "abstract": _strip_jats(item.get("abstract") or ""),
        "authors": authors,
        "published": published,
        "announce_date": published,
        "announce_type": "chemrxiv",
        "primary_category": cats[0] if cats else "chemrxiv",
        "categories": cats or ["chemrxiv"],
        "source": "chemrxiv",
        "doi": doi,
        "matched_query_category": matched_cat or "chemrxiv",
    }


def fetch_openengage_day(
    day: date,
    category_ids: list[tuple[str, str]],
    *,
    user_agent: str,
    page_size: int = 50,
    pause: float = 1.0,
) -> list[dict[str, Any]]:
    """Fetch one calendar day of ChemRxiv items for given OpenEngage category ids."""
    start = f"{day.isoformat()}T00:00:00.000Z"
    end = f"{(day + timedelta(days=1)).isoformat()}T00:00:00.000Z"
    by_id: dict[str, dict[str, Any]] = {}
    headers = {
        "User-Agent": user_agent,
        "Origin": "https://chemrxiv.org",
        "Referer": "https://chemrxiv.org/",
    }
    for cid, cname in category_ids:
        skip = 0
        while True:
            params = {
                "limit": page_size,
                "skip": skip,
                "sort": "PUBLISHED_DATE_DESC",
                "searchDateFrom": start,
                "searchDateTo": end,
                "categoryIds": cid,
            }
            url = f"{OPENENGAGE_BASE}/items?" + urllib.parse.urlencode(params)
            data = _urlopen_json(url, headers=headers)
            items = data.get("itemHits") or data.get("items") or data.get("content") or []
            # Some responses wrap as {itemHits: [{item: {...}}]}
            batch = []
            for hit in items:
                raw = hit.get("item") if isinstance(hit, dict) and "item" in hit else hit
                if isinstance(raw, dict):
                    batch.append(_normalize_openengage_item(raw, cname))
            for p in batch:
                existing = by_id.get(p["id"])
                if existing is None:
                    p["matched_query_categories"] = [p.pop("matched_query_category")]
                    by_id[p["id"]] = p
                else:
                    mq = p.pop("matched_query_category")
                    if mq not in existing["matched_query_categories"]:
                        existing["matched_query_categories"].append(mq)
            if len(batch) < page_size:
                break
            skip += page_size
            time.sleep(pause)
        time.sleep(pause)
    return list(by_id.values())


def _crossref_posted_day(item: dict[str, Any]) -> str:
    for key in ("posted", "issued", "created"):
        parts = ((item.get(key) or {}).get("date-parts") or [[]])[0]
        if parts:
            y = parts[0]
            m = parts[1] if len(parts) > 1 else 1
            d = parts[2] if len(parts) > 2 else 1
            return f"{y:04d}-{m:02d}-{d:02d}"
    return ""


def _normalize_crossref_item(item: dict[str, Any]) -> dict[str, Any]:
    doi = (item.get("DOI") or "").strip()
    title = ((item.get("title") or [""])[0] or "").strip()
    abstract = _strip_jats(item.get("abstract") or "")
    authors = []
    for a in item.get("author") or []:
        name = " ".join(x for x in [a.get("given"), a.get("family")] if x).strip()
        if name:
            authors.append(name)
    published = _crossref_posted_day(item)
    resource = ((item.get("resource") or {}).get("primary") or {}).get("URL") or ""
    url = resource or (f"https://doi.org/{doi}" if doi else "")
    return {
        "id": f"chemrxiv:{doi}" if doi else f"chemrxiv:{title[:40]}",
        "url": url,
        "title": title,
        "abstract": abstract,
        "authors": authors,
        "published": published,
        "announce_date": published,
        "announce_type": "chemrxiv",
        "primary_category": "chemrxiv",
        "categories": ["chemrxiv"],
        "source": "chemrxiv",
        "doi": doi,
        "matched_query_categories": ["chemrxiv"],
    }


def _keyword_match(paper: dict[str, Any], keywords: list[str]) -> bool:
    blob = f"{paper.get('title', '')} {paper.get('abstract', '')}".lower()
    return any(k.lower() in blob for k in keywords)


def fetch_crossref_day(
    day: date,
    *,
    mailto: str = "arxiv-digest@localhost",
    page_size: int = 100,
    pause: float = 0.4,
    keywords: list[str] | None = None,
    keyword_filter: bool = True,
) -> list[dict[str, Any]]:
    """Fetch ChemRxiv preprints posted on ``day`` via Crossref DOI prefix."""
    day_s = day.isoformat()
    cursor = "*"
    papers: list[dict[str, Any]] = []
    while True:
        params = {
            "rows": min(page_size, 1000),
            "cursor": cursor,
            "mailto": mailto,
            "filter": ",".join(
                [
                    f"prefix:{CHEMRXIV_PREFIX}",
                    "type:posted-content",
                    f"from-posted-date:{day_s}",
                    f"until-posted-date:{day_s}",
                ]
            ),
        }
        url = CROSSREF_WORKS + "?" + urllib.parse.urlencode(params)
        data = _urlopen_json(url)
        message = data.get("message") or {}
        items = message.get("items") or []
        for item in items:
            papers.append(_normalize_crossref_item(item))
        next_cursor = message.get("next-cursor")
        if not items or not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
        time.sleep(pause)

    if keyword_filter and keywords:
        before = len(papers)
        papers = [p for p in papers if _keyword_match(p, keywords)]
        print(
            f"  ChemRxiv Crossref {day_s}: {before} posted → "
            f"{len(papers)} after keyword filter",
            flush=True,
        )
    else:
        print(f"  ChemRxiv Crossref {day_s}: {len(papers)} papers", flush=True)
    return papers


def scrape_chemrxiv_day(cfg: dict[str, Any], day: date) -> list[dict[str, Any]]:
    """Scrape relevant ChemRxiv papers for one calendar day."""
    cx = cfg.get("chemrxiv") or {}
    if not cx.get("enabled", True):
        return []

    cat_names = [
        c["name"] if isinstance(c, dict) else c
        for c in (cx.get("categories") or DEFAULT_CATEGORY_NAMES)
    ]
    keywords = list(cx.get("relevance_keywords") or DEFAULT_KEYWORDS)
    keyword_filter = bool(cx.get("keyword_filter", True))
    ua = (cfg.get("arxiv") or {}).get("user_agent", "Mozilla/5.0 arxiv-digest/1.0")
    mailto = cx.get("mailto") or "arxiv-digest@localhost"

    print(f"Fetching ChemRxiv for {day.isoformat()}…", flush=True)
    available = try_openengage_categories(user_agent=ua)
    if available is not None:
        matched = _match_category_ids(available, cat_names)
        if matched:
            print(
                "  OpenEngage categories: "
                + ", ".join(name for _, name in matched),
                flush=True,
            )
            papers = fetch_openengage_day(day, matched, user_agent=ua)
            print(f"  -> {len(papers)} ChemRxiv papers", flush=True)
            return papers
        print("  OpenEngage reachable but no configured categories matched.", flush=True)

    return fetch_crossref_day(
        day,
        mailto=mailto,
        keywords=keywords,
        keyword_filter=keyword_filter,
    )


def scrape_chemrxiv_range(
    cfg: dict[str, Any], start: date, end: date
) -> list[dict[str, Any]]:
    """Scrape ChemRxiv day-by-day over an inclusive range."""
    by_id: dict[str, dict[str, Any]] = {}
    d = start
    while d <= end:
        for p in scrape_chemrxiv_day(cfg, d):
            by_id[p["id"]] = p
        d += timedelta(days=1)
        time.sleep(0.5)
    return list(by_id.values())
