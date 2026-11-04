# ruff: noqa: T201, S603, S607  # command-line measurement script: prints its results; ps/pgrep read the server's RSS
"""Load and latency measurements against a running API (never against production).

    uv run python tests/load/run_load.py http://127.0.0.1:8010 --server-pid <pid> [--json out.json]

Deterministic endpoints run at several concurrency levels; the RAG chat runs few
requests because each one calls the local LLM. Ollama is also measured directly so
model time and HTTP time can be told apart. Writes go to whatever database the
target server uses: point it at a test database.
"""

import asyncio
import json
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

OLLAMA = "http://localhost:11434"


def pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1))))
    return round(ordered[k], 1)


def rss_mb(pid: int | None) -> float | None:
    if pid is None:
        return None
    children = subprocess.run(
        ["pgrep", "-P", str(pid)], capture_output=True, text=True
    ).stdout.split()
    target = children[-1] if children else str(pid)
    out = subprocess.run(
        ["ps", "-o", "rss=", "-p", target], capture_output=True, text=True
    ).stdout.strip()
    return round(int(out) / 1024, 1) if out else None


async def scenario(
    client: httpx.AsyncClient,
    name: str,
    make: Callable[[], tuple[str, str, dict[str, Any] | None]],
    total: int,
    concurrency: int,
    pid: int | None,
) -> dict[str, Any]:
    latencies: list[float] = []
    errors: dict[str, int] = {}
    queue = iter(range(total))
    rss_before = rss_mb(pid)

    async def worker() -> None:
        for _ in queue:
            method, url, body = make()
            started = time.perf_counter()
            try:
                response = await client.request(method, url, json=body)
                code: int | str = response.status_code
            except httpx.HTTPError as error:
                code = type(error).__name__
            latencies.append((time.perf_counter() - started) * 1000)
            if not (isinstance(code, int) and code < 400):
                errors[str(code)] = errors.get(str(code), 0) + 1

    started = time.perf_counter()
    await asyncio.gather(*(worker() for _ in range(concurrency)))
    duration = time.perf_counter() - started
    row = {
        "escenario": name,
        "concurrencia": concurrency,
        "peticiones": total,
        "duracion_s": round(duration, 2),
        "throughput_rps": round(total / duration, 1),
        "p50_ms": pct(latencies, 50),
        "p95_ms": pct(latencies, 95),
        "p99_ms": pct(latencies, 99),
        "max_ms": round(max(latencies), 1),
        "errores": errors,
        "rss_mb_antes": rss_before,
        "rss_mb_despues": rss_mb(pid),
    }
    print(json.dumps(row, ensure_ascii=False), flush=True)
    return row


async def ollama_direct() -> list[dict[str, Any]]:
    rows = []
    async with httpx.AsyncClient(timeout=120) as client:
        embed, gen_total, gen_model = [], [], []
        for i in range(20):
            started = time.perf_counter()
            r = await client.post(
                f"{OLLAMA}/api/embed",
                json={
                    "model": "qwen3-embedding:0.6b",
                    "input": [f"¿Puedo enviar baterías de litio? {i}"],
                },
            )
            embed.append((time.perf_counter() - started) * 1000)
            r.raise_for_status()
        for i in range(6):
            started = time.perf_counter()
            r = await client.post(
                f"{OLLAMA}/api/chat",
                json={
                    "model": "qwen3:8b",
                    "think": False,
                    "stream": False,
                    "options": {"temperature": 0, "num_predict": 120},
                    "messages": [
                        {
                            "role": "user",
                            "content": f"Resume en dos frases la cadena de frío. ({i})",
                        }
                    ],
                },
            )
            gen_total.append((time.perf_counter() - started) * 1000)
            data = r.json()
            gen_model.append(data.get("total_duration", 0) / 1e6)
        rows.append(
            {
                "escenario": "ollama /api/embed (1 texto)",
                "peticiones": 20,
                "p50_ms": pct(embed, 50),
                "p95_ms": pct(embed, 95),
                "max_ms": round(max(embed), 1),
            }
        )
        rows.append(
            {
                "escenario": "ollama /api/chat (120 tokens)",
                "peticiones": 6,
                "p50_ms": pct(gen_total, 50),
                "p95_ms": pct(gen_total, 95),
                "modelo_p50_ms": pct(gen_model, 50),
            }
        )
    for row in rows:
        print(json.dumps(row, ensure_ascii=False), flush=True)
    return rows


async def main() -> None:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8010"
    pid = int(sys.argv[sys.argv.index("--server-pid") + 1]) if "--server-pid" in sys.argv else None
    out = Path(sys.argv[sys.argv.index("--json") + 1]) if "--json" in sys.argv else None
    api = base + "/api/v1"

    def chat(text: str) -> Callable[[], tuple[str, str, dict[str, Any]]]:
        return lambda: (
            "POST",
            f"{api}/chat/messages",
            {"client_id": str(uuid.uuid4()), "message_id": str(uuid.uuid4()), "text": text},
        )

    quote = {
        "origen": "Bogotá D.C.",
        "destino": "Medellín",
        "alto_cm": 40,
        "ancho_cm": 50,
        "largo_cm": 60,
        "peso_real_kg": 45.5,
        "valor_declarado_cop": 12500000,
    }
    profile_quote = {
        "origen": "Bogotá D.C.",
        "destino": "Barranquilla",
        "peso_real_kg": 6.2,
        "alto_cm": 20,
        "ancho_cm": 30,
        "largo_cm": 40,
    }
    plan = [
        ("GET /health", lambda: ("GET", base + "/health", None), 1000, 1),
        ("GET /health", lambda: ("GET", base + "/health", None), 2000, 50),
        ("GET /tracking/{guia}", lambda: ("GET", f"{api}/tracking/TLX-984021-CO", None), 1000, 1),
        ("GET /tracking/{guia}", lambda: ("GET", f"{api}/tracking/TLX-984021-CO", None), 2000, 50),
        ("POST /cotizar", lambda: ("POST", f"{api}/cotizar", quote), 1000, 50),
        ("POST /cotizaciones", lambda: ("POST", f"{api}/cotizaciones", profile_quote), 1000, 50),
        ("POST /chat (tracking, sin LLM)", chat("¿Dónde está mi guía TLX-984021-CO?"), 300, 1),
        ("POST /chat (tracking, sin LLM)", chat("¿Dónde está mi guía TLX-984021-CO?"), 1000, 20),
        ("POST /chat (tracking, sin LLM)", chat("¿Dónde está mi guía TLX-984021-CO?"), 1000, 50),
        ("POST /chat (RAG + LLM)", chat("¿Puedo enviar baterías de litio?"), 8, 1),
        ("POST /chat (RAG + LLM)", chat("¿Puedo enviar baterías de litio?"), 8, 4),
    ]
    rows = []
    async with httpx.AsyncClient(timeout=180, limits=httpx.Limits(max_connections=100)) as client:
        for name, make, total, concurrency in plan:
            rows.append(await scenario(client, name, make, total, concurrency, pid))
    rows += await ollama_direct()
    if out:
        out.write_text(json.dumps(rows, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
