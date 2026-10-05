"""OpenRouter connectivity check: one tiny structured-output request with the configured model."""

import json
import time

import httpx

from gridcast.ctl.shell import console, env

SCHEMA = {
    "name": "hypothesis_probe",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "statement": {"type": "string"},
            "suspect_entity": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["statement", "suspect_entity", "confidence"],
        "additionalProperties": False,
    },
}
PROMPT = (
    "GridCast's forecast pipeline slowed 10x right after feature-service was upgraded from 1.6.0 "
    "to 1.7.0, and PostgreSQL statement counts rose 600x. Give one falsifiable hypothesis."
)


def check() -> bool:
    values = env()
    key, model = values.get("OPENROUTER_API_KEY", ""), values.get("OPENROUTER_MODEL", "")
    base = values.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    if not key:
        console.print("[red]OPENROUTER_API_KEY is empty[/red] — set it in gridcast/.env")
        return False
    if not model:
        console.print("[red]OPENROUTER_MODEL is empty[/red] — e.g. deepseek/deepseek-v4-flash")
        return False
    t0 = time.perf_counter()
    response = httpx.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}", "X-Title": "GridCast / Lumis wiring check"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": PROMPT}],
            "response_format": {"type": "json_schema", "json_schema": SCHEMA},
            "max_tokens": 400,
            "usage": {"include": True},
        },
        timeout=60,
    )
    elapsed = time.perf_counter() - t0
    if response.status_code != 200:
        console.print(f"[red]OpenRouter returned {response.status_code}[/red]: {response.text[:400]}")
        return False
    body = response.json()
    content = body["choices"][0]["message"].get("content") or ""
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        console.print(f"[yellow]model answered but not with valid JSON[/yellow]: {content[:300]}")
        return False
    usage = body.get("usage", {})
    console.print(f"[green]OpenRouter OK[/green] model={body.get('model', model)} "
                  f"latency={elapsed:.1f}s tokens={usage.get('total_tokens')} "
                  f"cost=${usage.get('cost', 'n/a')}")
    console.print_json(json.dumps(parsed))
    return True
