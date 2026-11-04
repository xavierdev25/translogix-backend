# ruff: noqa: T201  # command-line measurement script: prints its results
"""End-to-end AI evaluation through the web chat (needs a running API with Ollama).

    uv run python tests/ai_eval/run_eval.py http://127.0.0.1:8010 [--json out.json]

Not part of pytest: it calls the real local LLM and takes ~1 minute. Each question uses
a fresh client, so the conversation context cannot help. Exit code 1 if a NO_ANSWER
case receives an affirmative answer (a hallucination is worse than an escalation).
"""

import asyncio
import json
import statistics
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

GOLDEN = Path(__file__).with_name("golden_set.json")


async def ask(client: httpx.AsyncClient, base: str, question: str) -> tuple[dict[str, Any], float]:
    body = {"client_id": str(uuid.uuid4()), "message_id": str(uuid.uuid4()), "text": question}
    started = time.perf_counter()
    response = await client.post(f"{base}/api/v1/chat/messages", json=body)
    elapsed = (time.perf_counter() - started) * 1000
    response.raise_for_status()
    return response.json()["reply"], elapsed


def judge(case: dict[str, Any], reply: dict[str, Any]) -> tuple[str, bool]:
    text = " ".join(b["text"] for b in reply["blocks"] if b["type"] == "TEXT").lower()
    cited = any(b["type"] == "CITATIONS" for b in reply["blocks"])
    answered = cited and bool(text)
    if case["expect"] == "ANSWER":
        ok = answered and any(k in text for k in case["keywords"])
        return ("ACIERTO" if ok else ("ESCALA" if reply["status"] == "ESCALATED" else "FALLO")), ok
    return ("ACIERTO" if not answered else "ALUCINA"), not answered


async def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8010"
    out = Path(sys.argv[sys.argv.index("--json") + 1]) if "--json" in sys.argv else None
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))["casos"]
    rows = []
    async with httpx.AsyncClient(timeout=120) as client:
        for case in cases:
            reply, ms = await ask(client, base, case["q"])
            verdict, ok = judge(case, reply)
            meta = reply["meta"]
            prim = ((meta.get("escalation") or {}).get("primary") or {}).get("id_criterio")
            rows.append(
                {
                    "id": case["id"],
                    "expect": case["expect"],
                    "veredicto": verdict,
                    "ok": ok,
                    "conf": meta.get("confidence_score"),
                    "intent": meta["intent"],
                    "escala": prim,
                    "ms": round(ms),
                }
            )
            print(
                f"{case['id']} {case['expect']:9} {verdict:8} conf={meta.get('confidence_score')} "
                f"intent={meta['intent']} esc={prim} {round(ms)} ms  {case['q'][:60]}"
            )
    answer = [r for r in rows if r["expect"] == "ANSWER"]
    refuse = [r for r in rows if r["expect"] == "NO_ANSWER"]
    latencies = sorted(r["ms"] for r in rows)
    summary = {
        "aciertos": sum(r["ok"] for r in rows),
        "total": len(rows),
        "responde_cuando_debe": f"{sum(r['ok'] for r in answer)}/{len(answer)}",
        "se_abstiene_cuando_debe": f"{sum(r['ok'] for r in refuse)}/{len(refuse)}",
        "alucinaciones": sum(r["veredicto"] == "ALUCINA" for r in rows),
        "latencia_ms": {"p50": statistics.median(latencies), "max": latencies[-1]},
    }
    print(json.dumps(summary, ensure_ascii=False))
    if out:
        out.write_text(
            json.dumps({"resumen": summary, "casos": rows}, ensure_ascii=False, indent=1)
        )
    return 1 if summary["alucinaciones"] else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
