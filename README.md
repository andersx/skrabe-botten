# skrabe-botten - an AI slop-grenade 🧨 for curating preprints

What is my purpose? You scrape papers.

Daily pharma / computational chemistry digest. Every morning it pulls **four preprint sources**, triages them with DeepSeek, and posts the keepers to Discord.

## What runs daily

At **07:30 Europe/Copenhagen**, cron runs:

```bash
python -m arxiv_digest run --last-24h --discord
```

That scrapes everything announced or published in the **past 24 hours** from:

| Source | How | What we keep |
|--------|-----|----------------|
| **arXiv** | RSS (`cs.LG`, `physics.chem-ph`, `q-bio.BM`, `physics.bio-ph`) | `new` + `cross` whose listing day falls in the window |
| **ChemRxiv** | OpenEngage API, or **Crossref** fallback if Cloudflare 403s | Keyword-filtered computational / medchem papers |
| **bioRxiv** | CSHL API | Configured categories + keyword filter |
| **medRxiv** | Same API family | Configured categories + keyword filter |

Then:

1. **DeepSeek** (`deepseek-flash`) triages every paper (keep / drop, P1–P3, category tags, one-line takeaway)
2. Discord gets **P1 + P2** with a core-topic filter (drops soft-only tags like property-prediction-only / `other_pharma_ml`-only)
3. High priority (P1) is marked with 🔥; P2 is unmarked

Outputs land under `out/` (raw JSON, full digest markdown, curated markdown + JSON). Logs: `out/logs/cron.log`.

## Setup

```bash
cd ~/dev/arxiv-digest   # or wherever you cloned skrabe-botten
python3 -m venv .venv
.venv/bin/pip install -e .
cp .env.example .env
# Edit .env — see keys below
```

| `.env` key | Purpose |
|------------|---------|
| `DEEPSEEK_API_KEY` | DeepSeek API key |
| `DISCORD_BOT_TOKEN` | Bot token from the Discord Developer Portal |
| `DISCORD_CHANNEL` | Text channel name to post in (without `#`) |
| `DISCORD_CHANNEL_ID` | Optional snowflake id (preferred if the name is ambiguous) |

Tune sources, categories, and keywords in `config.yaml`. Triage instructions live in `prompts/`.

## Commands

```bash
# Morning job (all four sources, past 24h) + Discord
.venv/bin/python -m arxiv_digest run --last-24h --discord

# Same scrape/curate without posting
.venv/bin/python -m arxiv_digest run --last-24h

# Single arXiv announce day (+ ChemRxiv/bioRxiv/medRxiv for that calendar day)
.venv/bin/python -m arxiv_digest run --day 2026-10-05

# Date range via arXiv Atom submittedDate (+ other sources day-by-day)
.venv/bin/python -m arxiv_digest scrape --from 2026-09-22 --to 2026-10-06
.venv/bin/python -m arxiv_digest curate --day 2026-09-22_to_2026-10-06

# Post an already curated day
.venv/bin/python -m arxiv_digest discord --day 2026-10-07
```

**Note:** arXiv’s Atom `submittedDate` filter does **not** match https://arxiv.org/list/cs.LG/recent. Daily mode uses RSS (`https://rss.arxiv.org/rss/<cat>`), which matches the website announce day.

## Schedule

```bash
chmod +x scripts/install_cron.sh
./scripts/install_cron.sh
```

Installs:

`30 7 * * * TZ=Europe/Copenhagen … run --last-24h --discord`

Check with `crontab -l`. After a run: `tail -f out/logs/cron.log`.

## Discord

1. Create a bot in the [Discord Developer Portal](https://discord.com/developers/applications); set `DISCORD_BOT_TOKEN` in `.env`.
2. Invite it with **Send Messages** (and **Embed Links** if you want).
3. Set `DISCORD_CHANNEL` (name) and/or `DISCORD_CHANNEL_ID` in `.env`.

Posts are flat markdown (no link previews): source tag + title link + takeaway.

## Outputs

| Path | Contents |
|------|----------|
| `out/raw/YYYY-MM-DD.json` | All scraped papers (all sources) |
| `out/digest/YYYY-MM-DD.md` | Full abstract dump |
| `out/curated/YYYY-MM-DD.md` | LLM-ranked digest |
| `out/curated/YYYY-MM-DD.json` | Structured curation + token usage |
| `out/logs/cron.log` | Cron stdout/stderr |

## Cost

A busy weekday (arXiv + preprints → DeepSeek) is typically on the order of **a few cents** with `deepseek-flash`. Exact usage is logged in each curated JSON.
