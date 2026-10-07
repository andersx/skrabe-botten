from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    root = project_root()
    load_dotenv(root / ".env")
    path = config_path or (root / "config.yaml")
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["_root"] = root
    cfg["_out"] = root / cfg.get("output", {}).get("dir", "out")
    return cfg


def llm_credentials(cfg: dict[str, Any]) -> tuple[str, str, str]:
    llm = cfg.get("llm", {})
    api_key = os.getenv("LLM_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or ""
    base_url = os.getenv("LLM_BASE_URL") or llm.get("base_url", "https://api.deepseek.com")
    model = os.getenv("LLM_MODEL") or llm.get("model", "deepseek-flash")
    return api_key, base_url.rstrip("/"), model
