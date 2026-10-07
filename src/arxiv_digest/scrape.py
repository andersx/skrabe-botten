from __future__ import annotations

import email.utils
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, time as dt_time, timedelta, timezone
from html import unescape
from typing import Any
from zoneinfo import ZoneInfo

from .chemrxiv_scrape import scrape_chemrxiv_range
from .rxiv_scrape import scrape_rxiv_range


ARXIV_NS = "http://arxiv.org/schemas/atom"
COPENHAGEN = ZoneInfo("Europe/Copenhagen")
ARXIV_TZ = ZoneInfo("America/New_York")
# RSS pubDate is a date stamp at midnight US/Eastern for the announce listing day
# (not the 20:00 ET process clock). Example: "Wed, 07 Oct 2026 00:00:00 -0400".
ARXIV_RSS_DATE_TIME = dt_time(0, 0)
ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}
DEFAULT_ANNOUNCE_TYPES = ("new", "cross")


def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", unescape(s or "")).strip()


def _urlopen_with_retries(
    req: urllib.request.Request,
    *,
    timeout: int = 90,
    max_retries: int = 8,
) -> bytes:
    """GET with retries for arXiv 429 / transient 5xx."""
    delay = 5.0
    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            last_err = exc
            if exc.code not in (429, 500, 502, 503, 504):
                raise
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            wait = delay
            if retry_after:
                try:
                    wait = max(wait, float(retry_after))
                except ValueError:
                    pass
            print(
                f"  arXiv HTTP {exc.code}; sleeping {wait:.0f}s "
                f"(retry {attempt + 1}/{max_retries})...",
                flush=True,
            )
            time.sleep(wait)
            delay = min(delay * 2, 120.0)
        except urllib.error.URLError as exc:
            last_err = exc
            print(
                f"  network error ({exc.reason}); sleeping {delay:.0f}s "
                f"(retry {attempt + 1}/{max_retries})...",
                flush=True,
            )
            time.sleep(delay)
            delay = min(delay * 2, 120.0)
    assert last_err is not None
    raise last_err


def _parse_pub_date(pub: str) -> date | None:
    """Parse RSS pubDate into a calendar date (arXiv announce day)."""
    if not pub:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(pub)
        # Announce listings are labeled in US/Eastern; use the calendar date
        # from the pubDate string's local offset (e.g. -0400), not UTC.
        return dt.date()
    except (TypeError, ValueError, IndexError):
        return None


def _extract_abstract(description: str) -> str:
    text = unescape(description or "")
    # Typical: "arXiv:.... Announce Type: new \nAbstract: ...."
    m = re.search(r"Abstract:\s*(.*)$", text, re.S | re.I)
    if m:
        return clean_text(m.group(1))
    # Strip announce header lines if present
    text = re.sub(r"^arXiv:\S+\s*Announce Type:\s*\S+\s*", "", text, flags=re.I)
    return clean_text(text)


def _extract_authors(item: ET.Element) -> list[str]:
    # dc:creator may appear multiple times, or as one comma-separated field
    creators = []
    for el in item:
        if el.tag.endswith("creator") and el.text:
            creators.append(clean_text(el.text))
    if not creators:
        return []
    # Sometimes a single "Last, First; Last2, First2"
    if len(creators) == 1 and ";" in creators[0]:
        return [clean_text(a) for a in creators[0].split(";") if clean_text(a)]
    return creators


def fetch_category_rss(
    cat: str,
    *,
    user_agent: str = "arxiv-digest/1.0",
) -> list[dict[str, Any]]:
    """Fetch current RSS listing for a category (matches arXiv /list/.../recent)."""
    url = f"https://rss.arxiv.org/rss/{cat}"
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    data = _urlopen_with_retries(req, timeout=90)
    root = ET.fromstring(data)

    papers: list[dict[str, Any]] = []
    for item in root.findall(".//item"):
        link = item.findtext("link") or ""
        arxiv_id = link.rstrip("/").split("/abs/")[-1] if "/abs/" in link else ""
        title = clean_text(item.findtext("title") or "")
        description = item.findtext("description") or ""
        abstract = _extract_abstract(description)
        pub = item.findtext("pubDate") or ""
        announce_day = _parse_pub_date(pub)
        announce_type = item.findtext(f"{{{ARXIV_NS}}}announce_type") or ""
        if not announce_type:
            m = re.search(r"Announce Type:\s*(\S+)", description, re.I)
            announce_type = (m.group(1) if m else "").lower()
        else:
            announce_type = announce_type.lower()

        cats = [c.text for c in item.findall("category") if c.text]
        # Primary is typically first category in RSS
        primary = cats[0] if cats else cat
        authors = _extract_authors(item)

        papers.append(
            {
                "id": arxiv_id,
                "url": link or (f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else ""),
                "title": title,
                "abstract": abstract,
                "authors": authors,
                "published": announce_day.isoformat() if announce_day else "",
                "announce_date": announce_day.isoformat() if announce_day else "",
                "announce_type": announce_type,
                "primary_category": primary,
                "categories": cats,
                "matched_query_category": cat,
            }
        )
    return papers


def scrape_day(
    cfg: dict[str, Any],
    *,
    announce_day: date | None = None,
) -> tuple[date, list[dict[str, Any]]]:
    """Scrape one arXiv announcement day via RSS (same as the website recent list).

    If announce_day is None, uses the newest date present in the feeds.
    Only that single day is kept (no catch-up of older days).
    Includes announce types new + cross by default (excludes replacements),
    matching the website entry counts.
    """
    arxiv_cfg = cfg.get("arxiv", {})
    pause = float(arxiv_cfg.get("request_pause_seconds", 1.0))
    ua = arxiv_cfg.get("user_agent", "Mozilla/5.0 arxiv-digest/1.0")
    types = set(arxiv_cfg.get("announce_types", list(DEFAULT_ANNOUNCE_TYPES)))

    by_id: dict[str, dict[str, Any]] = {}
    dates_seen: set[date] = set()

    for cat_entry in cfg["categories"]:
        cat = cat_entry["id"] if isinstance(cat_entry, dict) else cat_entry
        print(f"Fetching RSS for {cat}...", flush=True)
        papers = fetch_category_rss(cat, user_agent=ua)
        print(f"  -> {len(papers)} items in feed", flush=True)
        for p in papers:
            if p.get("announce_date"):
                dates_seen.add(date.fromisoformat(p["announce_date"]))
            existing = by_id.get(p["id"])
            if existing is None:
                p["matched_query_categories"] = [p.pop("matched_query_category")]
                by_id[p["id"]] = p
            else:
                mq = p.pop("matched_query_category")
                if mq not in existing["matched_query_categories"]:
                    existing["matched_query_categories"].append(mq)
                # Prefer richer metadata
                if len(p.get("abstract") or "") > len(existing.get("abstract") or ""):
                    existing["abstract"] = p["abstract"]
                if not existing.get("authors") and p.get("authors"):
                    existing["authors"] = p["authors"]
        time.sleep(pause)

    if not dates_seen:
        target = announce_day or datetime.now(timezone.utc).date()
        selected = _merge_preprint_sources(cfg, target, target, [])
        _print_source_breakdown(target.isoformat(), selected)
        return target, selected

    latest = max(dates_seen)
    target = announce_day or latest
    if announce_day is not None and announce_day not in dates_seen:
        print(
            f"Warning: announcement date {announce_day} not in RSS "
            f"(available: {sorted(dates_seen)}). Returning 0 papers.",
            flush=True,
        )

    selected: list[dict[str, Any]] = []
    for p in by_id.values():
        if p.get("announce_date") != target.isoformat():
            continue
        if types and p.get("announce_type") not in types:
            continue
        selected.append(p)

    selected.sort(key=lambda p: (p.get("announce_type", ""), p["id"]))
    print(
        f"Announcement day {target.isoformat()}: {len(selected)} arXiv papers "
        f"(types={sorted(types)}; latest available={latest.isoformat()})",
        flush=True,
    )

    for p in selected:
        p.setdefault("source", "arxiv")
    selected = _merge_preprint_sources(cfg, target, target, selected)
    _print_source_breakdown(target.isoformat(), selected)
    return target, selected


def _merge_preprint_sources(
    cfg: dict[str, Any],
    start: date,
    end: date,
    selected: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Append ChemRxiv + bioRxiv + medRxiv papers for [start, end] (deduped)."""
    seen = {p["id"] for p in selected}
    for p in scrape_chemrxiv_range(cfg, start, end):
        if p["id"] not in seen:
            selected.append(p)
            seen.add(p["id"])
    for server in ("biorxiv", "medrxiv"):
        for p in scrape_rxiv_range(cfg, server, start, end):
            if p["id"] not in seen:
                selected.append(p)
                seen.add(p["id"])
    return selected


def _print_source_breakdown(label: str, papers: list[dict[str, Any]]) -> None:
    counts = {"arxiv": 0, "chemrxiv": 0, "biorxiv": 0, "medrxiv": 0, "other": 0}
    for p in papers:
        src = (p.get("source") or "arxiv").lower()
        if src in ("arxiv", "chemrxiv", "biorxiv", "medrxiv"):
            counts[src] += 1
        else:
            counts["other"] += 1
    parts = [
        f"{counts[s]} {s}"
        for s in ("arxiv", "chemrxiv", "biorxiv", "medrxiv", "other")
        if counts[s]
    ]
    print(
        f"Combined {label}: {len(papers)} papers"
        + (f" ({', '.join(parts)})" if parts else ""),
        flush=True,
    )


def _arxiv_rss_listing_instant(announce_day: date) -> datetime:
    """Instant corresponding to arXiv RSS pubDate for an announce listing day."""
    return datetime.combine(announce_day, ARXIV_RSS_DATE_TIME, tzinfo=ARXIV_TZ)


def scrape_last_24h(
    cfg: dict[str, Any],
    *,
    now: datetime | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Scrape papers published/announced in the past 24 hours (Europe/Copenhagen clock).

    - arXiv: RSS items whose listing pubDate (midnight US/Eastern on announce_date)
      falls in (now-24h, now]. At a 07:30 Copenhagen cron this catches last night's
      batch (already in the feed; process runs ~20:00 ET the prior evening).
    - ChemRxiv / bioRxiv / medRxiv: calendar ``published`` dates overlapping that window
      (these APIs are date-granular, not timestamped)

    Output label is today's date in Copenhagen (for daily raw/curated filenames).
    """
    end = (now or datetime.now(tz=COPENHAGEN)).astimezone(COPENHAGEN)
    start = end - timedelta(hours=24)
    label = end.date().isoformat()
    day_start = start.date()
    day_end = end.date()

    print(
        f"Rolling 24h window {start.isoformat(timespec='minutes')} → "
        f"{end.isoformat(timespec='minutes')} (label={label})",
        flush=True,
    )

    arxiv_cfg = cfg.get("arxiv", {})
    pause = float(arxiv_cfg.get("request_pause_seconds", 1.0))
    ua = arxiv_cfg.get("user_agent", "Mozilla/5.0 arxiv-digest/1.0")
    types = set(arxiv_cfg.get("announce_types", list(DEFAULT_ANNOUNCE_TYPES)))

    by_id: dict[str, dict[str, Any]] = {}
    for cat_entry in cfg["categories"]:
        cat = cat_entry["id"] if isinstance(cat_entry, dict) else cat_entry
        print(f"Fetching RSS for {cat}...", flush=True)
        papers = fetch_category_rss(cat, user_agent=ua)
        print(f"  -> {len(papers)} items in feed", flush=True)
        for p in papers:
            existing = by_id.get(p["id"])
            if existing is None:
                p["matched_query_categories"] = [p.pop("matched_query_category")]
                p.setdefault("source", "arxiv")
                by_id[p["id"]] = p
            else:
                mq = p.pop("matched_query_category")
                if mq not in existing["matched_query_categories"]:
                    existing["matched_query_categories"].append(mq)
                if len(p.get("abstract") or "") > len(existing.get("abstract") or ""):
                    existing["abstract"] = p["abstract"]
                if not existing.get("authors") and p.get("authors"):
                    existing["authors"] = p["authors"]
        time.sleep(pause)

    selected: list[dict[str, Any]] = []
    for p in by_id.values():
        if types and p.get("announce_type") not in types:
            continue
        ad = p.get("announce_date")
        if not ad:
            continue
        try:
            announce_at = _arxiv_rss_listing_instant(date.fromisoformat(ad))
        except ValueError:
            continue
        if start < announce_at <= end:
            selected.append(p)

    selected.sort(key=lambda p: (p.get("announce_type", ""), p["id"]))
    print(
        f"arXiv in window: {len(selected)} papers (types={sorted(types)})",
        flush=True,
    )

    selected = _merge_preprint_sources(cfg, day_start, day_end, selected)
    _print_source_breakdown(label, selected)
    return label, selected


def fetch_category_atom_range(
    cat: str,
    start_day: date,
    end_day: date,
    *,
    page_size: int = 100,
    pause: float = 3.0,
    user_agent: str = "Mozilla/5.0 arxiv-digest/1.0",
) -> list[dict[str, Any]]:
    """Fetch papers by Atom API submittedDate in [start_day, end_day] inclusive (UTC)."""
    start_str = start_day.strftime("%Y%m%d") + "000000"
    end_str = (end_day + timedelta(days=1)).strftime("%Y%m%d") + "000000"
    query = f"cat:{cat} AND submittedDate:[{start_str} TO {end_str}]"
    papers: list[dict[str, Any]] = []
    start_idx = 0
    total_results: int | None = None

    while True:
        params = {
            "search_query": query,
            "start": start_idx,
            "max_results": page_size,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": user_agent})
        data = _urlopen_with_retries(req, timeout=90)
        root = ET.fromstring(data)

        if total_results is None:
            for el in root:
                if el.tag.endswith("totalResults"):
                    total_results = int(el.text or 0)
                    break

        batch: list[dict[str, Any]] = []
        for entry in root.findall("atom:entry", ATOM_NS):
            title = clean_text(entry.findtext("atom:title", default="", namespaces=ATOM_NS))
            abstract = clean_text(entry.findtext("atom:summary", default="", namespaces=ATOM_NS))
            published = entry.findtext("atom:published", default="", namespaces=ATOM_NS) or ""
            id_url = entry.findtext("atom:id", default="", namespaces=ATOM_NS) or ""
            arxiv_id = id_url.rstrip("/").split("/abs/")[-1] if id_url else ""
            authors = [
                clean_text(a.findtext("atom:name", default="", namespaces=ATOM_NS))
                for a in entry.findall("atom:author", ATOM_NS)
            ]
            cats = [c.get("term", "") for c in entry.findall("atom:category", ATOM_NS)]
            primary = entry.find("arxiv:primary_category", ATOM_NS)
            primary_cat = primary.get("term") if primary is not None else (cats[0] if cats else cat)
            batch.append(
                {
                    "id": arxiv_id,
                    "url": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else id_url,
                    "title": title,
                    "abstract": abstract,
                    "authors": authors,
                    "published": published[:10] if published else "",
                    "announce_date": published[:10] if published else "",
                    "announce_type": "submitted",
                    "primary_category": primary_cat,
                    "categories": cats,
                    "matched_query_category": cat,
                }
            )

        papers.extend(batch)
        if not batch:
            break
        if total_results is not None and len(papers) >= total_results:
            break
        if len(batch) < page_size:
            break
        start_idx += page_size
        time.sleep(pause)

    return papers


def scrape_range(
    cfg: dict[str, Any],
    start_day: date,
    end_day: date,
) -> tuple[str, list[dict[str, Any]]]:
    """Scrape an inclusive submittedDate range via Atom API (for multi-day backfills/tests).

    Returns (label, papers) where label is used for output filenames,
    e.g. 2026-09-28_to_2026-10-05.
    """
    if end_day < start_day:
        raise ValueError(f"end_day {end_day} is before start_day {start_day}")

    arxiv_cfg = cfg.get("arxiv", {})
    page_size = int(arxiv_cfg.get("page_size", 100))
    # arXiv asks for ≥3s between API calls; don't go faster even if config is lower
    pause = max(float(arxiv_cfg.get("request_pause_seconds", 3.0)), 3.0)
    ua = arxiv_cfg.get("user_agent", "Mozilla/5.0 arxiv-digest/1.0")

    by_id: dict[str, dict[str, Any]] = {}
    for cat_entry in cfg["categories"]:
        cat = cat_entry["id"] if isinstance(cat_entry, dict) else cat_entry
        print(
            f"Fetching Atom API {cat} submittedDate "
            f"{start_day.isoformat()} → {end_day.isoformat()}...",
            flush=True,
        )
        papers = fetch_category_atom_range(
            cat, start_day, end_day, page_size=page_size, pause=pause, user_agent=ua
        )
        print(f"  -> {len(papers)} hits", flush=True)
        for p in papers:
            existing = by_id.get(p["id"])
            if existing is None:
                p["matched_query_categories"] = [p.pop("matched_query_category")]
                by_id[p["id"]] = p
            else:
                mq = p.pop("matched_query_category")
                if mq not in existing["matched_query_categories"]:
                    existing["matched_query_categories"].append(mq)
        time.sleep(pause)

    for p in by_id.values():
        p.setdefault("source", "arxiv")
    papers = sorted(
        by_id.values(),
        key=lambda p: (p.get("published", ""), p["id"]),
        reverse=True,
    )
    papers = _merge_preprint_sources(cfg, start_day, end_day, papers)
    papers = sorted(
        papers,
        key=lambda p: (p.get("published", ""), p["id"]),
        reverse=True,
    )
    label = f"{start_day.isoformat()}_to_{end_day.isoformat()}"
    _print_source_breakdown(label, papers)
    return label, papers
