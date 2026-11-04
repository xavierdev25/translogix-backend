# TransLogix AI: Estado actual del backend (auditoría de solo lectura)

Fecha: 2026-11-03. No se modificó código, configuración, BD ni migraciones; solo se generaron
documentos en `docs/`. Estados: **PROBADO** (ejecutado contra la API real en esta auditoría o en la
del mismo día), **IMPLEMENTADO** (existe en código, sin ejecución), **SIMULADO**, **PARCIAL** y
**PENDIENTE**.

## 1. Verificación ejecutada en esta auditoría

| Verificación | Resultado |
|---|---|
| `GET /health` / `GET /ready` / `GET /docs` | 200 / 200 (`database: ok`, `ollama: ok`, `rag_index: ok`) / 200 |
| OpenAPI real vs. contrato (`app/contracts/api_spec.py`) | **18/18 operaciones** coinciden en método, ruta y `operationId`. Exportado a `docs/openapi.json` (16 rutas, 93 schemas) |
| `uv run pytest -q` | **385 passed**, 0 failed (suite de contratos; no usa la BD) |
| Llamadas reales a todos los endpoints (48 capturas) | Todas devolvieron el código esperado. Se ejecutaron **en una transacción revertida**: conteos de BD idénticos antes y después (36 conversaciones, 94 mensajes, 10 handoffs, 9 cotizaciones). Capturas en `docs/api-examples/` |
| Quick replies del Web Chat enviados como texto (8 títulos) | 5 OK, 3 fallan (ver R2) |
| Runner Bruno de 57 pruebas | **No se ejecutó** en esta auditoría porque persiste datos. Última ejecución, el mismo día: 57/57 PASS (`docs/FINAL_ACCEPTANCE_REPORT.md`) |

Dependencias disponibles: PostgreSQL 18 + pgvector 0.8.6 (`localhost:54329`), Ollama 0.40.14 con
`qwen3:8b` y `qwen3-embedding:0.6b`. **No disponibles:** credenciales de Meta (WhatsApp), PBX real,
ASR (Whisper no instalado), Bruno CLI, Docker (no verificado).

## 2. Estado por módulo

| Módulo | Estado | Evidencia / notas | Código |
|---|---|---|---|
| Sesiones de conversación | PROBADO | Crea o reutiliza la sesión por `client_id` (TTL 30 min); 404 ante sesión ajena; idempotencia por `message_id` (UNIQUE en BD) | `modules/conversation/service.py` |
| Orquestador de diálogo IA | PROBADO | Reglas deterministas primero; LLM solo como clasificador de último recurso y para redactar RAG; guardia anti prompt-injection | `modules/conversation/orchestrator.py`, `intents.py`, `slots.py` |
| Tracking (TMS) | PROBADO | Dataset **simulado** de 5 guías; vista sin PII; estados con etiquetas en español | `modules/tracking/` |
| Motor de cotización | PROBADO | Fórmula `provisional-v1`: las tarifas base que el PDF no define son supuestos visibles; no se inventan rutas | `modules/quotation/` |
| Cotización conversacional por slots | PROBADO | Un mensaje o varios turnos; montos en "12,5 millones", "450 mil", etc. | `slots.py` |
| RAG (pgvector + BM25) | PROBADO | 4 documentos, 84 chunks, dimensión 1024; citas; CRIT-001 sin respuesta cuando no hay evidencia | `modules/rag/` |
| Carga especial / mercancía peligrosa | PROBADO | RAG filtrado a DOC-OPS-2026-03 | `rag/service.py` |
| Motor de escalamiento | PROBADO | CRIT-001/002/003/005 observados. CRIT-004: el fixture marca las 5 cuentas como ESTANDAR, así que **no puede dispararse** hasta marcar un NIT GOLD | `modules/escalation/engine.py` |
| Handoffs (bandeja) | PROBADO | Listar, detalle y transiciones PENDING→ASSIGNED→CLOSED; resumen e historial sin PII | `escalation/handoff.py` |
| Respuesta del asesor al cliente | **PENDIENTE** | No hay endpoint ni WebSocket para que el asesor escriba al cliente | — |
| WebSocket (`handoff_status`) | **PENDIENTE** | El contrato existe (`contracts/ws.py`), pero la app no expone ninguna ruta WS | — |
| PQRS | PROBADO | Elegibilidad (días hábiles), checklist, liquidación preliminar, escalamiento | `modules/pqrs/service.py` |
| Adjuntos | PROBADO (PDF) | Tipo por contenido, límite 10 MB, nombre aleatorio, sha256. JPEG implementado, sin prueba en esta auditoría | `pqrs/service.py` (`AttachmentService`) |
| Adjuntos dentro del chat | IMPLEMENTADO | Los `attachments` con `declared_doc_code` se guardan en la sesión y se agregan al ticket PQRS creado por chat. Sin prueba E2E | `orchestrator.py` |
| Métricas por canal | PROBADO | Agregados desde `audit_events` | `db/repositories.py` |
| WhatsApp | SIMULADO | `/simulate` PROBADO. Webhook real (HMAC) IMPLEMENTADO, **no probado**: sin credenciales devuelve 503. El envío real a Meta **no existe** | `app/main.py` |
| PBX | SIMULADO | Solo transcripción; reconoce la guía dictada; `speech_text` sin dígitos. `audio_ref` → 503 (sin ASR). `PBX_MODE=REAL` se rechaza en la configuración | `app/main.py` |
| Autenticación administrativa | PROBADO | Token estático `X-Admin-Token` (`compare_digest`) | `shared/http.py` |
| Autenticación de usuarios / roles | **PENDIENTE** | No hay login, usuarios ni roles de asesor | — |
| Recordatorio por inactividad (`IDLE`) | **PENDIENTE** | La variable existe; no hay lógica | — |
| Caída del LLM | IMPLEMENTADO | El RAG responde con texto y opción de asesor; el clasificador cae a `DESCONOCIDA`. No se probó apagando Ollama en esta auditoría | `orchestrator.py`, `intents.py` |
| Rate limit del chat | IMPLEMENTADO | 30/min por usuario, en memoria y por proceso. No se probó el 429 en esta auditoría | `shared/http.py` |
| Logs sin PII | PROBADO (mismo día) | Formatter JSON con redacción; 0 coincidencias de PII y secretos en el log y la BD | `shared/logging.py` |
| Docker / despliegue | PENDIENTE de verificar | — | — |
| Pruebas automatizadas de servicios | **PARCIAL** | 385 tests de contratos. El orquestador, el servicio de conversación y `main.py` **no tienen tests pytest**; solo E2E | `backend/tests/` |

## 3. Diferencias entre la app real y el contrato 1.2.0

| Ítem | Contrato | App real | Impacto |
|---|---|---|---|
| `GET /chat/sessions/{id}/history` | Sin headers | Header **opcional** `X-Client-Id` (si viene y no es el dueño → 404) | El frontend debe enviarlo. Convertirlo en obligatorio requiere un cambio de contrato |
| `POST /channels/whatsapp/webhook` | Body `WhatsAppWebhookPayload` declarado | Lee el cuerpo crudo (necesario para el HMAC) y valida después; el OpenAPI real no declara el body | Solo documental |
| `GET /ready` | Schema de 200 declarado | El 200 no declara schema (devuelve `JSONResponse`); el 503 sí | Solo documental |
| `ErrorEnvelope` | Descrito en el contrato | No figura en `components` del OpenAPI real | Documentado a mano en `FRONTEND_HANDOFF.md` §G y §F |

## 4. Riesgos y problemas encontrados

| # | Severidad | Hallazgo | Recomendación |
|---|---|---|---|
| R1 | **Alta (bloqueante para un chat en vivo con asesor)** | No existe canal asesor→cliente (ni endpoint ni push). El cliente recibe "Te responderá en este mismo chat", pero no hay mecanismo para hacerlo | Implementar un endpoint de mensaje del asesor + WebSocket/SSE, o cambiar el texto |
| R2 | **Alta para la UX web** | `ChatMessageRequest` no tiene `interactive_reply_id`. Enviados como texto, 3 de 8 quick replies fallan: `novedad` y `otra_guia` → DESCONOCIDA; `cadena_frio` → respuesta RAG en vez de recotizar. `equipo_legal` solo funciona con el LLM | Añadir `interactive_reply_id` al contrato del Web Chat; mientras tanto, usar los textos alternativos de `FRONTEND_HANDOFF.md` §D.6 |
| R3 | Media (seguridad) | El historial se puede leer con solo el `session_id` si se omite `X-Client-Id` | Hacer obligatorio el header (contrato 1.3.0) |
| R4 | Media (negocio) | `CRIT003_MODE=claim_context` escala a `JURIDICO_PQRS` **cualquier** ticket (incluso una PETICION) sobre una guía con valor declarado > $5M | Revisar con negocio: limitar CRIT-003 a SINIESTRO/RECLAMO |
| R5 | Media (seguridad) | Un único token de administración compartido, sin identidad de asesor; la bandeja no puede vivir en un frontend público | Autenticación de asesores, o un BFF |
| R6 | Media (calidad) | No hay pruebas unitarias de orquestador, conversación ni rutas | Añadir tests con el patrón de transacción revertida |
| R7 | Baja | Los textos RAG pueden arrastrar Markdown o LaTeX del manual (`$-20^\circ\text{C}$`) | Limpiar en el backend o renderizar como texto plano |
| R8 | Baja | El `speech_text` de PBX lee los números dígito a dígito ("dos cero dos seis") | Conversor de números a palabras |
| R9 | Baja | `/docs`, `/openapi.json` y `/metrics/channels` son públicos | Proteger en producción |
| R10 | Baja | Rate limit en memoria, no compartido entre réplicas | Redis o el gateway |
| R11 | Baja | Sin índice vectorial (HNSW) en `rag_chunks.embedding`: irrelevante con 84 chunks | Añadirlo si el corpus crece |
| R12 | Baja | `CORS` sin `expose_headers`: el navegador no puede leer `X-Request-ID` | Usar `error.request_id`, o exponer el header |
| R13 | Info | Todos los datos son simulados o fixtures (TMS, cuentas). No hay pruebas de carga | No afirmar capacidad de producción |
| R14 | Info | El proyecto no está bajo control de versiones (sin `git init`). Ruff reporta 20 líneas largas (E501): 19 en `orchestrator.py` y 1 en `intents.py`; mypy estricto sin errores | Versionar antes de integrar |

## 5. Mapa del backend

```
backend/app/
  main.py                  rutas reales (18 operaciones), DI (Container), CORS, adaptadores WhatsApp/PBX
  contracts/               modelos Pydantic, OpenAPI del contrato, puertos (fuente de verdad, v1.2.0)
  modules/conversation/    service.py (sesión, idempotencia, auditoría), orchestrator.py, intents.py, slots.py
  modules/tracking/        TMS simulado + sanitizador de PII
  modules/quotation/       datos de tarifas/cobertura + motor provisional-v1
  modules/rag/             corpus/chunking, ingesta, búsqueda híbrida, generación con comprobaciones
  modules/escalation/      engine.py (reglas JSON), handoff.py, sentiment.py, accounts.py
  modules/pqrs/            elegibilidad, checklist, liquidación, adjuntos
  db/                      models.py (10 tablas), repositories.py, session.py
  shared/                  config, clock, http (errores, request-id, admin, rate limit), llm (Ollama), logging
backend/migrations/        Alembic (0001_initial_schema)
data/source, data/config, data/fixtures   insumos del reto, configuración y fixtures simulados
```

Persistencia (PostgreSQL): `conversations`, `messages` (UNIQUE `channel,message_id,direction`),
`handoffs`, `quotes`, `pqrs_tickets`, `attachments`, `audit_events`, `rag_documents` y
`rag_chunks` (`vector(1024)`). Solo `messages→conversations` y `rag_chunks→rag_documents` tienen
FK; el resto referencia `session_id` con índice. Consultas de inspección en
`docs/sql/pgadmin_inspection.sql`.

## 6. Documentos generados

| Archivo | Contenido |
|---|---|
| `docs/FRONTEND_HANDOFF.md` | Documento principal de integración (endpoints, contratos, chat, tipos TS, errores, seguridad) |
| `docs/FRONTEND_INTEGRATION_EXAMPLES.md` | Cliente TS mínimo y flujos completos con peticiones y respuestas reales |
| `docs/openapi.json` | OpenAPI exportado de la app real |
| `docs/api-examples/*.json` | 48 capturas reales completas (request + response) |
| `docs/BACKEND_CURRENT_STATE.md` | Este documento |
