"""Optional LLM-enriched diagnosis against any OpenAI-compatible endpoint.

Deliberately isolated: when no API key is configured (or the call fails) the
agent falls back to the deterministic runbook-template diagnosis, so the whole
self-healing loop works with zero external dependencies.
"""
from __future__ import annotations

import httpx

from . import runtime


def llm_available() -> bool:
    return bool(runtime.settings.llm_diagnosis_enabled and runtime.settings.openai_api_key)


def llm_diagnose(state: dict) -> str | None:
    settings = runtime.settings
    if not settings.llm_diagnosis_enabled or not settings.openai_api_key:
        return None
    runbook = state.get("runbook") or {}
    user_prompt = (
        "Service log lines:\n"
        + "\n".join(state.get("raw_log", []))
        + f"\n\nMatched runbook {runbook.get('rb_id')}: {runbook.get('title')}\n"
        + f"Prescribed fix: {runbook.get('fix_command')}\n\n"
        "In 3-4 sentences for a fellow on-call engineer: state the root cause, "
        "why the prescribed fix addresses it, and any residual risk."
    )
    try:
        response = httpx.post(
            f"{settings.openai_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json={
                "model": settings.openai_model,
                "temperature": 0.2,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a senior SRE writing terse, precise incident diagnoses.",
                    },
                    {"role": "user", "content": user_prompt},
                ],
            },
            timeout=25,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return None  # graceful fallback to the deterministic template
