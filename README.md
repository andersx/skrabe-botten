# skrabe-botten - an AI slop-grenade 🧨 for curating preprints

What is my purpose? You scrape papers.

Daily pharma computational chemistry digest (arXiv + ChemRxiv + bioRxiv + medRxiv).

Daily local pipeline:

1. Scrape **one arXiv announcement day** via RSS (same as the website “recent” list)
2. Save abstracts (`new` + `cross`; replacements skipped)
3. Send every paper to **DeepSeek** (`deepseek-flash`) for triage
4. Write a curated markdown digest

**Important:** The Atom API `submittedDate` filter does **not** match https://arxiv.org/list/cs.LG/recent. This tool uses `https://rss.arxiv.org/rss/<cat>` instead.

## Setup

```bash
cd ~/dev/arxiv-digest
python3 -m venv .venv
.venv/bin/pip install -e .
cp .env.example .env
# Edit .env and set DEEPSEEK_API_KEY from https://platform.deepseek.com
```

## Commands

```bash
# Scrape newest announcement day (e.g. Mon 5 Oct) + curate
.venv/bin/python -m arxiv_digest run

# Scrape only (latest announce day)
.venv/bin/python -m arxiv_digest scrape

# Scrape a specific announce day
.venv/bin/python -m arxiv_digest scrape --day 2026-10-05

# Curate an existing scrape
.venv/bin/python -m arxiv_digest curate
.venv/bin/python -m arxiv_digest curate --day 2026-10-05

# Post curated digest to Discord (#paper-botten)
.venv/bin/python -m arxiv_digest discord --day 2026-10-05

# Scrape + curate + Discord in one go
.venv/bin/python -m arxiv_digest run --discord
```

## ChemRxiv

Daily runs also pull **ChemRxiv** and merge it into the same curated digest:

- Preferred subjects: *Theoretical and Computational Chemistry*, *Biological and Medicinal Chemistry* (OpenEngage API when reachable)
- Fallback: Crossref DOI prefix `10.26434` + keyword filter (OpenEngage is often Cloudflare-blocked on servers)

Toggle / tune in `config.yaml` under `chemrxiv:`.

## Discord bot

1. Create a bot in the [Discord Developer Portal](https://discord.com/developers/applications), copy the token into `.env` as `STJERNEBOTTENS_DISCORD_TOKEN`.
2. Invite the bot to your server with **Send Messages** and **Embed Links**.
3. Create (or use) a text channel named `paper-botten` (configurable in `config.yaml` / `DISCORD_CHANNEL`).
4. Post with `python -m arxiv_digest discord` or `run --discord`.

The bot posts **P1–P2 only**, excluding keeps tagged solely `other_pharma_ml`. If the channel name exists in multiple servers, set `DISCORD_CHANNEL_ID` in `.env`.

## Schedule (every morning 07:30 Europe/Copenhagen)

Scrapes everything announced/published in the **past 24 hours** (arXiv RSS + ChemRxiv + bioRxiv + medRxiv), curates, and posts to Discord:

```bash
chmod +x scripts/install_cron.sh
./scripts/install_cron.sh
```

Manual equivalent:

```bash
.venv/bin/python -m arxiv_digest run --last-24h --discord
```

## Outputs

| Path | Contents |
|------|----------|
| `out/raw/YYYY-MM-DD.json` | All scraped papers |
| `out/digest/YYYY-MM-DD.md` | Full abstract dump |
| `out/curated/YYYY-MM-DD.md` | LLM-ranked pharma digest |
| `out/curated/YYYY-MM-DD.json` | Structured curation + token usage |
| `out/logs/cron.log` | Cron stdout/stderr |

## Categories scraped

- `cs.LG` — Machine Learning  
- `physics.chem-ph` — Chemical Physics  
- `q-bio.BM` — Biomolecules  
- `physics.bio-ph` — Biological Physics  

Edit `config.yaml` and `prompts/` to change categories or triage instructions.

## Cost

Sending all ~400 weekday papers to `deepseek-flash` is typically **~$0.07–0.15/day** (~$2–3/month). Usage and estimated USD are logged in each curated JSON.
