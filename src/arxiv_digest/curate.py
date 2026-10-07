from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from openai import OpenAI

from .config import llm_credentials, project_root


ALLOWED_LABELS = {
    "sbdd",
    "virtual_screening",
    "ligand_based_vs",
    "property_prediction",
    "protein_structure",
    "molecular_simulation",
    "binding_affinity",
    "other_pharma_ml",
}


def _load_prompts() -> tuple[str, str]:
    root = project_root()
    system = (root / "prompts" / "curate_system.txt").read_text(encoding="utf-8").strip()
    user_tmpl = (root / "prompts" / "curate_user.txt").read_text(encoding="utf-8")
    return system, user_tmpl


def _format_paper_block(p: dict[str, Any]) -> str:
    authors = ", ".join(p.get("authors", [])[:8])
    if len(p.get("authors", [])) > 8:
        authors += f", et al. ({len(p['authors'])} authors)"
    cats = ", ".join(p.get("categories") or [])
    source = p.get("source") or "arxiv"
    return (
        f"id: {p['id']}\n"
        f"source: {source}\n"
        f"title: {p['title']}\n"
        f"primary_category: {p.get('primary_category', '')}\n"
        f"categories: {cats}\n"
        f"authors: {authors}\n"
        f"abstract: {p.get('abstract', '')}\n"
    )


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def _normalize_result(item: dict[str, Any], fallback_id: str) -> dict[str, Any]:
    pid = str(item.get("id") or fallback_id)
    keep = bool(item.get("keep", False))
    cats = item.get("categories") or []
    if not isinstance(cats, list):
        cats = []
    cats = [c for c in cats if c in ALLOWED_LABELS]
    try:
        priority = int(item.get("priority", 3))
    except (TypeError, ValueError):
        priority = 3
    priority = min(3, max(1, priority))
    takeaway = str(item.get("takeaway") or "").strip()
    if not keep:
        cats = []
        priority = 3
        if not takeaway:
            takeaway = "Not relevant to pharma computational chemistry."
    return {
        "id": pid,
        "keep": keep,
        "categories": cats,
        "priority": priority,
        "takeaway": takeaway,
    }


def curate_papers(cfg: dict[str, Any], papers: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    api_key, base_url, model = llm_credentials(cfg)
    if not api_key:
        raise RuntimeError(
            "Missing DEEPSEEK_API_KEY (or LLM_API_KEY). Copy .env.example to .env and set your key."
        )

    llm = cfg.get("llm", {})
    batch_size = int(llm.get("batch_size", 25))
    temperature = float(llm.get("temperature", 0.2))
    max_retries = int(llm.get("max_retries", 3))
    # Off-peak defaults; Pro is ~4x Flash
    if "pro" in model.lower():
        price_in = float(llm.get("price_input_per_mtok_pro", 0.66))
        price_out = float(llm.get("price_output_per_mtok_pro", 1.98))
    else:
        price_in = float(llm.get("price_input_per_mtok", 0.15))
        price_out = float(llm.get("price_output_per_mtok", 0.60))

    system, user_tmpl = _load_prompts()
    client = OpenAI(api_key=api_key, base_url=base_url)
    print(f"Model: {model} @ {base_url} (est. ${price_in}/M in, ${price_out}/M out)", flush=True)

    results_by_id: dict[str, dict[str, Any]] = {}
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "batches": 0}

    for i in range(0, len(papers), batch_size):
        batch = papers[i : i + batch_size]
        blocks = "\n---\n".join(_format_paper_block(p) for p in batch)
        user = user_tmpl.replace("{papers_block}", blocks)
        usage["batches"] += 1
        print(f"Curating batch {usage['batches']} ({len(batch)} papers)...", flush=True)

        last_err: Exception | None = None
        parsed: dict[str, Any] | None = None
        for attempt in range(1, max_retries + 1):
            try:
                # Explicit non-thinking for predictable cost/latency on triage jobs
                create_kwargs = {
                    "model": model,
                    "temperature": temperature,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    "response_format": {"type": "json_object"},
                }
                try:
                    resp = client.chat.completions.create(
                        **create_kwargs,
                        extra_body={"thinking": {"type": "disabled"}},
                    )
                except Exception:
                    resp = client.chat.completions.create(**create_kwargs)
                content = resp.choices[0].message.content or ""
                if resp.usage:
                    usage["prompt_tokens"] += resp.usage.prompt_tokens or 0
                    usage["completion_tokens"] += resp.usage.completion_tokens or 0
                parsed = _extract_json(content)
                break
            except Exception as e:
                last_err = e
                print(f"  retry {attempt}/{max_retries}: {e}", flush=True)
                time.sleep(2 * attempt)
        if parsed is None:
            raise RuntimeError(f"Curation failed after retries: {last_err}")

        items = parsed.get("papers") if isinstance(parsed, dict) else None
        if not isinstance(items, list):
            raise RuntimeError(f"Unexpected LLM JSON shape: {parsed!r}")

        returned: dict[str, dict[str, Any]] = {}
        for it in items:
            if not isinstance(it, dict):
                continue
            norm = _normalize_result(it, "")
            if norm["id"]:
                returned[norm["id"]] = norm
        for p in batch:
            if p["id"] in returned:
                results_by_id[p["id"]] = returned[p["id"]]
            else:
                results_by_id[p["id"]] = {
                    "id": p["id"],
                    "keep": False,
                    "categories": [],
                    "priority": 3,
                    "takeaway": "Missing from model response; marked skip.",
                }

        time.sleep(0.5)

    est_cost = usage["prompt_tokens"] / 1e6 * price_in + usage["completion_tokens"] / 1e6 * price_out
    usage["estimated_usd"] = round(est_cost, 6)
    usage["model"] = model
    usage["base_url"] = base_url

    ordered = [results_by_id[p["id"]] for p in papers]
    return ordered, usage
