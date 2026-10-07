#!/usr/bin/env bash
# Install a daily cron job at 07:30 Europe/Copenhagen.
# Scrapes arXiv + ChemRxiv + bioRxiv + medRxiv for the past 24 hours,
# curates with DeepSeek, and posts the digest to Discord.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${ROOT}/.venv/bin/python"
LOG_DIR="${ROOT}/out/logs"
mkdir -p "${LOG_DIR}"

if [[ ! -x "${PYTHON}" ]]; then
  echo "Missing venv at ${ROOT}/.venv — run: python3 -m venv .venv && .venv/bin/pip install -e ."
  exit 1
fi

# Cron uses the machine timezone; pin TZ so 07:30 is always Copenhagen time.
CRON_LINE="30 7 * * * TZ=Europe/Copenhagen cd ${ROOT} && ${PYTHON} -m arxiv_digest run --last-24h --discord >> ${LOG_DIR}/cron.log 2>&1"

# Remove any prior arxiv_digest cron lines, then add ours
EXISTING="$(crontab -l 2>/dev/null || true)"
FILTERED="$(printf '%s\n' "${EXISTING}" | grep -v 'arxiv_digest' || true)"
{
  printf '%s\n' "${FILTERED}"
  printf '%s\n' "${CRON_LINE}"
} | crontab -

echo "Installed cron:"
echo "  ${CRON_LINE}"
echo
crontab -l | grep arxiv_digest || true
