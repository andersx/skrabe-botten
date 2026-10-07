from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from .config import load_config
from .curate import curate_papers
from .discord_post import post_digest
from .render import write_curated, write_curation_json, write_full_digest, write_raw
from .scrape import scrape_day, scrape_last_24h, scrape_range
from .store import load_day_from_store, load_range_days


def _parse_day(s: str | None) -> date | None:
    if not s:
        return None
    return date.fromisoformat(s)


def cmd_scrape(
    cfg: dict,
    announce_day: date | None = None,
    *,
    start: date | None = None,
    end: date | None = None,
    last_24h: bool = False,
) -> tuple[str, list]:
    out = cfg["_out"]
    if last_24h:
        if announce_day is not None or start is not None or end is not None:
            raise SystemExit("--last-24h cannot be combined with --day / --from / --to.")
        label, papers = scrape_last_24h(cfg)
        meta = {
            "lookback": "last_24h",
            "timezone": "Europe/Copenhagen",
            "source": ["rss.arxiv.org", "chemrxiv", "biorxiv", "medrxiv"],
            "announce_types": cfg.get("arxiv", {}).get("announce_types", ["new", "cross"]),
            "categories": [c["id"] if isinstance(c, dict) else c for c in cfg["categories"]],
            "chemrxiv": cfg.get("chemrxiv", {}).get("categories"),
            "biorxiv": cfg.get("biorxiv", {}).get("categories"),
            "medrxiv": cfg.get("medrxiv", {}).get("categories"),
        }
    elif start is not None or end is not None:
        if start is None or end is None:
            raise SystemExit("Both --from and --to are required for a date range.")
        label, papers = scrape_range(cfg, start, end)
        meta = {
            "lookback": "submittedDate_range",
            "source": ["export.arxiv.org/api", "chemrxiv", "biorxiv", "medrxiv"],
            "from": start.isoformat(),
            "to": end.isoformat(),
            "categories": [c["id"] if isinstance(c, dict) else c for c in cfg["categories"]],
            "chemrxiv": cfg.get("chemrxiv", {}).get("categories"),
            "biorxiv": cfg.get("biorxiv", {}).get("categories"),
            "medrxiv": cfg.get("medrxiv", {}).get("categories"),
        }
    else:
        day, papers = scrape_day(cfg, announce_day=announce_day)
        label = day.isoformat()
        meta = {
            "lookback": "latest_announcement_day",
            "source": ["rss.arxiv.org", "chemrxiv", "biorxiv", "medrxiv"],
            "announce_types": cfg.get("arxiv", {}).get("announce_types", ["new", "cross"]),
            "categories": [c["id"] if isinstance(c, dict) else c for c in cfg["categories"]],
            "requested_announce_day": announce_day.isoformat() if announce_day else None,
            "chemrxiv": cfg.get("chemrxiv", {}).get("categories"),
            "biorxiv": cfg.get("biorxiv", {}).get("categories"),
            "medrxiv": cfg.get("medrxiv", {}).get("categories"),
        }

    raw_path = write_raw(out, label, papers, meta)
    digest_path = write_full_digest(out, label, papers, cfg["categories"])
    print(f"Wrote {raw_path} ({len(papers)} papers)")
    print(f"Wrote {digest_path}")
    return label, papers


def _load_day_payloads(cfg: dict, label: str) -> tuple[list, list, dict]:
    out = cfg["_out"]
    raw_path = out / "raw" / f"{label}.json"
    curated_path = out / "curated" / f"{label}.json"
    if not raw_path.exists():
        raise SystemExit(f"Missing raw scrape: {raw_path}. Run scrape first.")
    if not curated_path.exists():
        raise SystemExit(f"Missing curated JSON: {curated_path}. Run curate first.")
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    curated = json.loads(curated_path.read_text(encoding="utf-8"))
    return raw["papers"], curated.get("curations", []), curated.get("usage", {})


def cmd_curate(cfg: dict, label: str, papers: list | None = None) -> None:
    out = cfg["_out"]
    if papers is None:
        raw_path = out / "raw" / f"{label}.json"
        if not raw_path.exists():
            raise SystemExit(f"Missing raw scrape: {raw_path}. Run scrape first.")
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
        papers = payload["papers"]

    curations, usage = curate_papers(cfg, papers)
    write_curation_json(out, label, curations, usage)
    curated_path = write_curated(
        out, label, papers, curations, cfg.get("section_order", []), usage
    )
    print(f"Wrote {curated_path}")
    print(
        f"Usage: in={usage['prompt_tokens']} out={usage['completion_tokens']} "
        f"est=${usage['estimated_usd']:.4f}"
    )


def cmd_discord(
    cfg: dict,
    label: str | None = None,
    *,
    start: date | None = None,
    end: date | None = None,
) -> None:
    if start is not None or end is not None:
        if start is None or end is None:
            raise SystemExit("Both --from and --to are required for a Discord date range.")
        rows = load_range_days(cfg["_out"], start, end)
        print(f"Posting {len(rows)} day(s) {start.isoformat()} → {end.isoformat()}…")
        for day, papers, curations, usage in rows:
            print(f"  {day}: {len(papers)} papers")
            post_digest(cfg, day, papers, curations, usage)
        return

    if label is None:
        raise SystemExit("Provide --day or --from/--to.")
    # Single-day / range-label file
    daily_raw = cfg["_out"] / "raw" / f"{label}.json"
    daily_cur = cfg["_out"] / "curated" / f"{label}.json"
    if daily_raw.exists() and daily_cur.exists() and "_to_" not in label:
        papers, curations, usage = _load_day_payloads(cfg, label)
    elif "_to_" not in label:
        papers, curations, usage = load_day_from_store(cfg["_out"], label)
    else:
        papers, curations, usage = _load_day_payloads(cfg, label)
    post_digest(cfg, label, papers, curations, usage)


def cmd_run(
    cfg: dict,
    announce_day: date | None = None,
    *,
    start: date | None = None,
    end: date | None = None,
    last_24h: bool = False,
    post_discord: bool = False,
) -> None:
    label, papers = cmd_scrape(
        cfg, announce_day, start=start, end=end, last_24h=last_24h
    )
    if not papers:
        print("No papers for that day/range; skipping curation.")
        empty_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "estimated_usd": 0.0,
            "model": cfg.get("llm", {}).get("model", ""),
            "batches": 0,
        }
        write_curation_json(cfg["_out"], label, [], empty_usage)
        write_curated(cfg["_out"], label, [], [], cfg.get("section_order", []), empty_usage)
        if post_discord:
            post_digest(cfg, label, [], [], empty_usage)
        return
    cmd_curate(cfg, label, papers)
    if post_discord:
        papers_loaded, curations, usage = _load_day_payloads(cfg, label)
        post_digest(cfg, label, papers_loaded, curations, usage)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Daily arXiv pharma digest (RSS announce day, or Atom date range)"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to config.yaml (default: project config.yaml)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_time_args(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--day",
            type=str,
            default=None,
            help="Announcement date YYYY-MM-DD (RSS mode; default: newest day in feed).",
        )
        p.add_argument(
            "--from",
            dest="date_from",
            type=str,
            default=None,
            help="Range start YYYY-MM-DD (Atom submittedDate; use with --to).",
        )
        p.add_argument(
            "--to",
            dest="date_to",
            type=str,
            default=None,
            help="Range end YYYY-MM-DD inclusive (Atom submittedDate; use with --from).",
        )
        p.add_argument(
            "--last-24h",
            action="store_true",
            help=(
                "Scrape arXiv + ChemRxiv + bioRxiv + medRxiv published/announced "
                "in the past 24 hours (Europe/Copenhagen). Used by the morning cron."
            ),
        )

    p_scrape = sub.add_parser("scrape", help="Scrape RSS day or Atom date range")
    add_time_args(p_scrape)

    p_curate = sub.add_parser("curate", help="Curate existing raw JSON")
    p_curate.add_argument(
        "--day",
        type=str,
        default=None,
        help="Raw file label, e.g. 2026-10-05 or 2026-09-28_to_2026-10-05",
    )

    p_run = sub.add_parser("run", help="Scrape then curate with DeepSeek")
    add_time_args(p_run)
    p_run.add_argument(
        "--discord",
        action="store_true",
        help="After curation, post P1/P2 core digest to Discord (#paper-botten).",
    )

    p_discord = sub.add_parser(
        "discord",
        help="Post an existing curated day (or date range, one message per day) to Discord",
    )
    p_discord.add_argument(
        "--day",
        type=str,
        default=None,
        help="Day label, e.g. 2026-10-05 (default: newest curated JSON)",
    )
    p_discord.add_argument(
        "--from",
        dest="date_from",
        type=str,
        default=None,
        help="Post each calendar day from this date (use with --to).",
    )
    p_discord.add_argument(
        "--to",
        dest="date_to",
        type=str,
        default=None,
        help="Post each calendar day through this date (use with --from).",
    )

    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    cfg["_out"].mkdir(parents=True, exist_ok=True)
    (cfg["_out"] / "logs").mkdir(parents=True, exist_ok=True)

    day_label = getattr(args, "day", None)
    start = _parse_day(getattr(args, "date_from", None))
    end = _parse_day(getattr(args, "date_to", None))
    # --day for curate may be a range label (e.g. 2026-09-28_to_2026-10-05)
    announce_day = None
    if day_label and "_to_" not in day_label:
        announce_day = _parse_day(day_label)

    last_24h = bool(getattr(args, "last_24h", False))

    if args.command == "scrape":
        cmd_scrape(cfg, announce_day, start=start, end=end, last_24h=last_24h)
    elif args.command == "curate":
        if day_label is None:
            raw_dir = cfg["_out"] / "raw"
            files = sorted(raw_dir.glob("*.json")) if raw_dir.exists() else []
            if not files:
                raise SystemExit("No --day given and no raw/*.json found. Run scrape first.")
            day_label = files[-1].stem
            print(f"Using newest raw file: {day_label}")
        cmd_curate(cfg, day_label)
    elif args.command == "discord":
        if start is not None or end is not None:
            cmd_discord(cfg, start=start, end=end)
        else:
            if day_label is None:
                curated_dir = cfg["_out"] / "curated"
                files = sorted(curated_dir.glob("*.json")) if curated_dir.exists() else []
                day_files = [f for f in files if "_to_" not in f.stem and "comparison" not in f.stem]
                files = day_files or files
                if not files:
                    raise SystemExit("No --day given and no curated/*.json found. Run curate first.")
                day_label = files[-1].stem
                print(f"Using newest curated file: {day_label}")
            cmd_discord(cfg, day_label)
    elif args.command == "run":
        cmd_run(
            cfg,
            announce_day,
            start=start,
            end=end,
            last_24h=last_24h,
            post_discord=bool(getattr(args, "discord", False)),
        )
    else:
        parser.error(f"Unknown command {args.command}")


if __name__ == "__main__":
    main()
