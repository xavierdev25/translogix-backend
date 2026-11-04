# TransLogix AI: Bruno collection

57 requests in 10 folders. Each one maps to a route in `backend/app/contracts/api_spec.py`
(the source of `contracts/openapi.yaml`), and no endpoint was invented. JSON bodies come
from `contracts/examples/` wherever an example exists.

The collection is **generated**: edit `scripts/bruno_collection.py`, not the `.bru` files.

```bash
python3 scripts/bruno_collection.py write   # regenerate the .bru files
```

## Import into Bruno

1. Bruno → *Open Collection* → choose `bruno/translogix-ai/`.
2. Choose the **Local** environment.
3. Set the secret variable `adminToken` to the value of `ADMIN_TOKEN` in your `.env`. Bruno keeps
   it locally and never writes it to the `.bru` files. Only the folders that call
   `/admin` and `/handoffs` need it.
4. Run the folders **in order** (Runner → *Run Collection*). Some requests reuse values
   captured by earlier ones:

| Variable | Captured by | Used by |
|---|---|---|
| `sessionId` | first chat turn of each flow | turns in the same session, history |
| `handoffId` | `06 Escalation / 01 Retenida… CRIT-005` | GET/PATCH handoff |
| `attachmentId` | `07 PQRS / 01 Subir factura` | `07 PQRS / 02 Siniestro` |
| `clientId` / `messageId` | pre-request script (random UUID v4) | every chat request |

Sessions that get escalated stay with the advisor, so the scenarios that escalate generate a
fresh `clientId` first.

## Running without Bruno (same requests, same checks)

```bash
python3 scripts/bruno_collection.py run --json /tmp/bruno_run.json
```

This uses only the standard library. It reads `ADMIN_TOKEN` from the environment or from `.env`,
never prints it, and exits non-zero if any check fails. `BASE_URL` overrides `http://localhost:8000`.

## Prerequisites

- API running: `cd backend && uv run uvicorn app.main:app --port 8000`.
- PostgreSQL with pgvector, migrated (`uv run alembic upgrade head`).
- Ollama running with `qwen3:8b` and `qwen3-embedding:0.6b`. Run the RAG reindex
  (`05 RAG / 01`) at least once.
- The `05 RAG` requests and the RAG-backed channel requests call the LLM and take 2–6 s each.

## Side effects (all in the local development database)

| Folder | Writes |
|---|---|
| 03 Quotation | `quotes` |
| 04–10 chat / channels | `conversations`, `messages`, `audit_events`; `handoffs` when escalating |
| 05 RAG / Reindexar | `rag_documents`, `rag_chunks` (idempotent by hash; `force:false`) |
| 06 Escalation / PATCH | changes the **status** of the handoff created by the same folder |
| 07 PQRS | `attachments` (file in `var/attachments/`), `pqrs_tickets` |

No request deletes data. WhatsApp and PBX are **SIMULATED**: nothing is sent to Meta or to a PBX.
