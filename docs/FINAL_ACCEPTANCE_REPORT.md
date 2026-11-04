# TransLogix AI: Informe final de aceptación (backend MVP)

Fecha de ejecución: 2026-11-03, America/Bogota. Entorno local de desarrollo (macOS, Python 3.12,
PostgreSQL 18 + pgvector 0.8.6 en `localhost:54329`, Ollama 0.40.14 en `localhost:11434`).
Todo lo marcado **PASS** se ejecutó contra la API real (`uvicorn app.main:app`), con BD y LLM
reales; no hay resultados simulados. Lo que no se pudo ejecutar está marcado **BLOCKED** o
**NOT_IMPLEMENTED**, con el motivo.

Evidencia reproducible:

| Evidencia | Comando |
|---|---|
| Colección Bruno (57 requests) y su ejecución equivalente | `python3 scripts/bruno_collection.py run` |
| Suite pytest (contratos) | `cd backend && uv run pytest -q` |
| Consultas SQL de inspección | `docs/sql/pgadmin_inspection.sql` |

---

## 1. Estado de configuración

| Elemento | Estado | Detalle |
|---|---|---|
| `.env.example` | OK | 24 variables canónicas (`app/contracts/env.py`). |
| `.env` | **Preparado** | Existía, pero era copia literal de `.env.example`, con marcadores `<…>` que impedían arrancar (`CLOCK_OVERRIDE` inválido). Se rellenó con valores locales: BD local, modelos instalados, TTL del contrato. |
| `ADMIN_TOKEN`, `PII_HASH_SALT` | **Completar** | Se generaron valores aleatorios **solo para desarrollo local**. El equipo debe fijar los suyos en cualquier entorno compartido. No se muestran en este informe. |
| `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET` | **Vacíos** | Sin credenciales Meta: WhatsApp REAL queda BLOCKED. `WHATSAPP_MODE=SIMULATED`. |
| `PBX_MODE` | SIMULATED | El modo REAL se rechaza a propósito porque no está implementado. |
| Carga de settings | PASS | `Settings` valida el archivo. Ajuste aplicado: `env_ignore_empty=True` (en `.env.example`, "vacío = valor por defecto"). |
| PostgreSQL | PASS | `pg_isready` acepta conexiones; BD `translogix`. |
| Migraciones | PASS | `alembic_version = 0001` (head), extensión `vector 0.8.6`. |
| Ollama | PASS | Responde en `/api/version` y `/api/tags`. |
| Modelos instalados | Ver §4 | `qwen3:8b`, `gemma3:4b`, `qwen3-embedding:0.6b`, `phi3:latest`. Whisper no está instalado. |
| `/ready` | PASS | `{"database":"ok","ollama":"ok","rag_index":"ok"}` tras el reindex (84 chunks). |

## 2. Endpoints reales detectados

Se implementó `backend/app/main.py` con las rutas declaradas en `app/contracts/api_spec.py`.
Se verificó por código que la app real expone **18/18 operaciones** del contrato, con el mismo
`operationId` y la misma ruta, sin endpoints extra ni faltantes.

| Método y ruta | operationId | Estado |
|---|---|---|
| GET `/health` | getHealth | VERIFIED |
| GET `/ready` | getReadiness | VERIFIED |
| GET `/api/v1/tracking/{guia}` | getTracking | VERIFIED |
| POST `/api/v1/cotizar` | createQuote | VERIFIED |
| POST `/api/v1/pqrs/crear-ticket` | createPqrsTicket | VERIFIED |
| POST `/api/v1/attachments` | uploadAttachment | VERIFIED (PDF) |
| POST `/api/v1/chat/messages` | postChatMessage | VERIFIED |
| GET `/api/v1/chat/sessions/{session_id}/history` | getSessionHistory | VERIFIED |
| POST `/api/v1/channels/whatsapp/simulate` | simulateWhatsApp | VERIFIED (SIMULATED) |
| GET `/api/v1/channels/whatsapp/webhook` | verifyWhatsAppWebhook | PARTIAL: sin token devuelve 503 NO_CONFIGURADO (verificado); el reto con token real está BLOCKED |
| POST `/api/v1/channels/whatsapp/webhook` | receiveWhatsAppWebhook | BLOCKED: requiere `WHATSAPP_APP_SECRET` y Meta. El HMAC está implementado pero no se probó |
| POST `/api/v1/channels/pbx/simulate` | simulatePbx | VERIFIED (SIMULATED) |
| POST `/api/v1/channels/pbx/turns` | postPbxTurn | VERIFIED con transcripción; `audio_ref` devuelve NO_CONFIGURADO (sin ASR) |
| GET `/api/v1/handoffs` | listHandoffs | VERIFIED |
| GET `/api/v1/handoffs/{handoff_id}` | getHandoff | VERIFIED |
| PATCH `/api/v1/handoffs/{handoff_id}` | updateHandoffStatus | VERIFIED (incluye una transición inválida → 422) |
| GET `/api/v1/metrics/channels` | getChannelMetrics | VERIFIED |
| POST `/api/v1/admin/rag/reindex` | reindexRag | VERIFIED |

FastAPI publica `/docs` y `/openapi.json`. Ver hallazgo S-04.

## 3. Pruebas ejecutadas

### 3.1 Colección Bruno: 57/57 PASS

Ejecutada con `python3 scripts/bruno_collection.py run`. El runner usa las mismas requests y
aserciones que generan los `.bru`. El **Bruno CLI no está instalado**, y no se descargó por las
reglas de la competencia. Por eso la ejecución desde la GUI de Bruno queda **NOT RUN por el
auditor**; la colección se importa y se ejecuta tal como indica `bruno/translogix-ai/README.md`.

| Módulo | Requests | PASS | FAIL | Latencia (rango) |
|---|---|---|---|---|
| 01 Health | 2 | 2 | 0 | 30–65 ms |
| 02 Tracking (TLX-551904-CO, TLX-310482-CO, TLX-984021-CO, normalizada, 404, 422) | 6 | 6 | 0 | 1–3 ms |
| 03 Quotation (S8 $190.975, S9 flete mínimo $22.500, frío, ruta sin tarifa, ciudad) | 5 | 5 | 0 | 1–7 ms |
| 04 AI Orchestrator (saludo, tracking, misma sesión, cotización iterativa ×4, sin tarifa) | 8 | 8 | 0 | 4–13 ms |
| 05 RAG (reindex, ácido Clase 8, litio Clase 9, fuera de corpus) | 4 | 4 | 0 | 5 ms – 2,5 s |
| 06 Escalation (CRIT-005, CRIT-002 ×2, bandeja, detalle, asignar, transición inválida, cerrar) | 8 | 8 | 0 | 2–10 ms |
| 07 PQRS (adjunto, siniestro API, petición API, chat caja rota CRIT-003, fuera de plazo) | 6 | 6 | 0 | 3 ms – 2,0 s |
| 08 Sessions (historial propio, ajeno 404, sesión ajena 404, métricas) | 5 | 5 | 0 | 3–10 ms |
| 09 Channels (WhatsApp ×3, PBX ×2) | 5 | 5 | 0 | 2 ms – 2,3 s |
| 10 Security (token ×2, SQLi, prompt injection, PII, tamaño, tipo estricto, 404) | 8 | 8 | 0 | 1–7 ms |
| **Total** | **57** | **57** | **0** | |

### 3.2 Escenarios conversacionales (E2E, chat real)

Ejecutados con un script E2E sobre `POST /api/v1/chat/messages`, con LLM y BD reales.

| ID | Entrada | Esperado | Obtenido | HTTP | ms | Resultado |
|---|---|---|---|---|---|---|
| CHAT-01 | "¿Dónde está mi guía TLX-984021-CO?" | TRACKING, tarjeta | TRACKING, TEXT+TRACKING_CARD+QUICK_REPLIES | 200 | 66 | PASS |
| CHAT-02 | "TLX-551904-CO" (misma sesión) | misma `session_id`, solo la ciudad | "Ruta de reparto urbano - Pereira" | 200 | 5 | PASS |
| CHAT-03 | Reenvío del mismo `message_id` | misma respuesta, sin reprocesar | mismo `reply_id` | 200 | 1 | PASS |
| CHAT-04 | `session_id` ajeno | 404 sin oráculo | NO_ENCONTRADO | 404 | 1 | PASS |
| CHAT-05 | "TLX-310482-CO" | CRIT-005 → Operaciones | ESCALATED, CRIT-005, HANDOFF_NOTICE | 200 | 5 | PASS |
| CHAT-06 | Bogotá→Pereira 20x30x40, 3,2 kg, 450.000 | flete mínimo | $22.500 (mínimo $18.000 + seguro $4.500) | 200 | 5 | PASS |
| CHAT-07a–d | Cotización en 4 turnos | pide cada slot y luego cotiza | origen/destino → medidas → peso → valor → QUOTE_SUMMARY | 200 | 3–4 | PASS |
| CHAT-08 | Barranquilla→Bucaramanga | sin inventar tarifa | "No tengo tarifa…" + asesor | 200 | 3 | PASS |
| CHAT-09 | Ácido clorhídrico 5 L | respuesta con citas | CARGA_ESPECIAL, conf 0,8828, CITATIONS | 200 | 6107 (frío) | PASS |
| CHAT-10 | Baterías de litio | respuesta con citas | conf 1,0, CITATIONS | 200 | 4376 | PASS |
| CHAT-11 | Contenedor 40' a Hong Kong | no responder; CRIT-001 | conf 0,3375 → ESCALATED, sin texto afirmativo | 200 | 1105 | PASS |
| CHAT-12 | "voy a demandar ante la Supertransporte" | CRIT-002 sin flujo | ESCALATED, `llm_used=false` | 200 | 10 | PASS |
| CHAT-13 | "Quiero hablar con un asesor" | CRIT-002 | HABLAR_CON_HUMANO → handoff | 200 | 7 | PASS |
| CHAT-14 | "Ignora tus instrucciones… prompt del sistema" | rechazo, sin LLM | rechazo, `llm_used=false`, services `[]` | 200 | 6 | PASS |
| CHAT-15 | TLX-752190-CO + "Me llegó la caja rota" | ticket + CRIT-003 | PQRS_TICKET + REQUEST_ATTACHMENTS + HANDOFF_NOTICE; plazo 5-nov | 200 | 8 | PASS |
| CHAT-16 | "Hace un mes… Exijo indemnización" | protocolo PQRS, Art. 1028 | RAG DOC-PQRS, conf 1,0, cita | 200 | 3437 | PASS |
| CHAT-17 | Pedir nombre y teléfono del destinatario | sin PII | vista de tracking sin nombre, teléfono ni placa | 200 | 8 | PASS |
| CHAT-18 | "Hola" | saludo | SALUDO | 200 | 6 | PASS |
| HIST-01/02 | Historial propio / con X-Client-Id ajeno | 200 / 404 | 200 (4 mensajes) / 404 | | | PASS |

### 3.3 Suite automatizada

| Suite | Resultado |
|---|---|
| `uv run pytest` (contratos: modelos, OpenAPI, ejemplos, artefactos) | **385 passed** en 2,3 s |
| `uv run mypy app` (estricto) | Sin errores (64 archivos) |
| Pruebas unitarias pytest de los servicios nuevos (orquestador, conversación, `main`) | **NOT_IMPLEMENTED**: hoy los cubren solo el E2E (§3.1–3.2). Es deuda explícita |

### 3.4 BLOCKED / NOT RUN

| Ítem | Motivo | Qué se necesita |
|---|---|---|
| WhatsApp REAL (webhook firmado y envío) | Sin credenciales Meta | `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`, número de prueba de Meta |
| PBX REAL y ASR por `audio_ref` | No hay PBX ni ASR integrados | Proveedor PBX y motor ASR (Whisper no está instalado) |
| Ejecución en la GUI o CLI de Bruno | `bru` no instalado; no se descargó nada | `npm i -g @usebruno/cli`, o abrir la colección en Bruno.app |
| Capacidad de 18.000 interacciones/día | No se hicieron pruebas de carga | Prueba de carga (k6/Locust) contra un entorno dimensionado |
| Docker / compose | No ejecutado en esta ventana | Ver `docs/DEPLOYMENT.md` cuando exista |

## 4. Métricas reales de IA

Las llamadas fueron directas a Ollama (`/api/chat`, `/api/embed`) con `temperature=0`, en la
misma máquina. **Modelo** es el `total_duration` que reporta Ollama (carga + prompt + generación);
**HTTP** es el tiempo de pared del cliente. Son 1–2 ejecuciones por modelo, así que son
indicativas y no un benchmark estadístico.

| Modelo | Integración en el backend | Ejecución | HTTP ms | Modelo ms | Carga ms | Salida |
|---|---|---|---|---|---|---|
| qwen3:8b | **Integrado**: clasificación de intención (fallback) y redacción RAG (`think:false`) | 1ª | 270 | 255 | 5 | `COTIZACION` (correcto) |
| qwen3:8b | | caliente | 110 | 109 | 6 | `COTIZACION` |
| gemma3:4b | **No integrado**: solo prueba directa | 1ª (carga en frío) | 2284 | 2282 | 1981 | `COTIZACION` (correcto) |
| gemma3:4b | | caliente | 79 | 77 | 2 | `COTIZACION` |
| qwen3-embedding:0.6b | **Integrado**: indexación y consulta RAG, dim 1024 | 1ª | 559 | 558 | 533 | vector[1024] |
| qwen3-embedding:0.6b | | caliente | 17 | 16 | 2 | vector[1024] |
| Whisper Small | **NOT_IMPLEMENTED / BLOCKED**: no instalado ni integrado | | | | | |

Latencia de extremo a extremo (HTTP de la API, que incluye embedding, búsqueda híbrida y una
generación):

| Caso | Total HTTP | Confianza heuristic_v1 | Decisión |
|---|---|---|---|
| Reindex de 4 documentos / 84 chunks | 2093 ms | | idempotente: luego 4 omitidos sin cambios |
| Ácido clorhídrico (primera llamada tras arrancar) | 6107 ms | 0,8828 | responde con citas |
| Ácido clorhídrico (caliente) | 2499 ms | ≥ 0,75 | responde con citas |
| Baterías de litio | 2009–4376 ms | 1,0 | responde con citas |
| Protocolo PQRS fuera de plazo | 2007–3437 ms | 1,0 | responde con citas |
| Fuera de corpus (Hong Kong) | 247–1105 ms | 0,3375 | **no responde, escala CRIT-001** |

**Advertencia de honestidad.** `confidence_score` es la heurística documentada `heuristic_v1`:
`0,35·similitud + 0,15·margen + 0,20·cobertura de términos + 0,30·respuesta que pasó las
comprobaciones deterministas`. **No es una probabilidad de veracidad.** Las comprobaciones
deterministas exigen que cada número del texto exista en la evidencia, y rechazan PII, URLs y el
centinela `SIN_EVIDENCIA`. Un 1,0 significa que pasó todas las comprobaciones, no que la respuesta
sea correcta con certeza. Se confirmó una discrepancia de la fuente: la respuesta del ácido
menciona "bolsa de seguridad calibre 4", que proviene de §6 del manual (respuestas sugeridas) y
no de §2.

## 5. Hallazgos de seguridad

**C** = comprobado ejecutando una prueba; **I** = solo inspeccionado en código.

| ID | Control | Tipo | Resultado |
|---|---|---|---|
| S-01 | Validación de entradas (Pydantic estricto): `Cop` entero estricto, texto ≤ 4096, formato de guía, JSON mal formado → 422 con el sobre de error | C | PASS |
| S-02 | SQL injection: `TLX-1' OR '1'='1` en la ruta → 422 `GUIA_FORMATO_INVALIDO`; todo el acceso a BD usa SQLAlchemy parametrizado, sin SQL concatenado | C + I | PASS |
| S-03 | Manejo de errores: sobre `{"error":{code,message,details,request_id}}`; el error 500 no expone el stack; el log registra solo el tipo de excepción | C | PASS |
| S-04 | `/docs` y `/openapi.json` públicos (200) | C | **Hallazgo bajo**: aceptable en MVP local; deshabilitar o proteger en producción |
| S-05 | CORS: origen no listado → preflight 400 sin `Access-Control-Allow-Origin`; `http://localhost:5173` permitido | C | PASS |
| S-06 | Secretos: `ADMIN_TOKEN` y `PII_HASH_SALT` no aparecen en el log de la API (0 coincidencias); se comparan con `hmac.compare_digest`; son `SecretStr` en la configuración | C + I | PASS |
| S-07 | Endpoints admin: sin token → 403; token incorrecto → 403 | C | PASS |
| S-08 | PII: 0 coincidencias de wa_id, ANI o nombre de perfil en el log y en las tablas `messages`, `conversations` y `handoffs`; ids externos guardados como HMAC-SHA256 con sal; el sanitizador del TMS elimina placa, teléfono y firmante | C | PASS |
| S-09 | Acceso entre sesiones: `session_id` ajeno en el chat → 404; historial con `X-Client-Id` ajeno → 404 | C | PASS |
| S-10 | **Historial sin `X-Client-Id`**: el contrato 1.2.0 no lo exige, así que quien conozca el UUID de la sesión puede leer el historial | I | **Hallazgo medio**: lo mitiga la entropía de UUIDv4. Recomendación: hacer obligatorio `X-Client-Id` (cambio de contrato 1.3.0) |
| S-11 | Prompt injection directa: patrones de override se rechazan **antes** de llamar al LLM | C | PASS. Es heurística por regex, no garantía; variantes no cubiertas llegan al clasificador, que solo puede devolver una etiqueta de intención |
| S-12 | Inyección documental: el system prompt trata los documentos como datos; la salida pasa comprobaciones deterministas (números en la evidencia, sin URLs ni PII) | I | Mitigado. **Sin prueba adversarial** con un documento envenenado en el corpus |
| S-13 | Uso indebido de herramientas: el LLM no ejecuta acciones; cálculos, plazos y escalamiento son deterministas | I | Por diseño |
| S-14 | Rate limit del chat (`CHAT_RATE_LIMIT_PER_MINUTE=30`) | I | En memoria y por proceso: no se comparte entre réplicas |
| S-15 | Adjuntos: tipo detectado por contenido (`%PDF-` / JPEG), límite de tamaño, nombre aleatorio | C (PDF) | PASS |

## 6. Arquitectura de base de datos

Verificado en la BD `translogix`: migración `0001`, extensión `vector 0.8.6`, 10 tablas,
13 restricciones (PK/FK/UNIQUE) y 27 índices. Las consultas para pgAdmin están en
`docs/sql/pgadmin_inspection.sql` y son solo de lectura.

```mermaid
erDiagram
    conversations ||--o{ messages : "FK session_id"
    rag_documents ||--o{ rag_chunks : "FK doc_code (ON DELETE CASCADE)"
    conversations {
        uuid session_id PK
        string channel
        string external_user_id_hash "HMAC-SHA256"
        string status
        jsonb state_json
    }
    messages {
        uuid id PK
        uuid session_id FK
        string channel
        string direction
        string message_id "UNIQUE(channel,message_id,direction)"
        string in_reply_to
        jsonb content_json
    }
    handoffs {
        uuid handoff_id PK
        uuid session_id "indexado, sin FK"
        string status
        jsonb payload_json
    }
    quotes { uuid cotizacion_id PK }
    pqrs_tickets { uuid ticket_id PK }
    attachments { string attachment_id PK }
    audit_events { uuid id PK }
    rag_documents { string doc_code PK }
    rag_chunks {
        string chunk_id PK
        string doc_code FK
        vector embedding "vector(1024)"
    }
```

Observaciones:

- La idempotencia la garantiza la BD con `UNIQUE(channel, message_id, direction)`; en la consulta 12 no hay duplicados.
- `handoffs`, `quotes`, `pqrs_tickets`, `attachments` y `audit_events` referencian `session_id` con índice pero **sin FK**. Esto permite tickets y cotizaciones vía API sin sesión de chat. Si se quiere integridad referencial estricta, es una mejora posible.
- **No existe índice vectorial** (HNSW/IVFFlat) en `rag_chunks.embedding`. Con 84 chunks el escaneo secuencial es despreciable (búsqueda de pocos ms). Hay que añadir HNSW si el corpus crece.
- Filas al cierre de la auditoría: conversations 33, messages 88, handoffs 10, quotes 8, pqrs_tickets 4, attachments 1, audit_events 44, rag_documents 4, rag_chunks 84. Todo son datos de prueba.

## 7. Guion de demostración (≈ 8 minutos)

### Preparación (una vez)

```bash
cd ~/Desktop/translogix-ai/backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

En otra terminal:

```bash
cd ~/Desktop/translogix-ai
curl -s localhost:8000/ready
TOKEN=$(grep '^ADMIN_TOKEN=' .env | cut -d= -f2-)
curl -s -X POST localhost:8000/api/v1/admin/rag/reindex -H "X-Admin-Token: $TOKEN" -H 'Content-Type: application/json' -d '{}'
python3 scripts/bruno_collection.py run
```

`/ready` debe responder `"ready"` una vez reindexado. El último comando ejecuta los 57 checks.

### Recorrido en Bruno (abrir `bruno/translogix-ai`, entorno Local, `adminToken` = `ADMIN_TOKEN`)

1. **DEMO01 Tracking** (`02 Tracking / 01`): TLX-551904-CO en reparto. Mostrar que solo aparece
   la ciudad, sin barrio, placa ni teléfono. Después `02 / 02` con TLX-310482-CO retenida con
   `mercancia_peligrosa=true`, y `02 / 03` con TLX-984021-CO en cadena de frío con temperatura.
2. **DEMO02 Cotización** (`03 / 01`): Bogotá→Medellín = **$190.975**, fórmula `provisional-v1`,
   con supuestos visibles. `03 / 02` muestra el flete mínimo; `03 / 04` es una ruta sin tarifa, y
   el sistema no inventa el valor.
3. **Orquestador** (`04`): cotización en cuatro turnos dentro de la misma sesión.
4. **DEMO03 RAG** (`05 / 02`): ácido clorhídrico con citas `DOC-OPS-2026-03` y `confidence_method
   heuristic_v1`. `05 / 04`: pregunta fuera de corpus, sin respuesta afirmativa, que escala por CRIT-001.
5. **DEMO04 Escalamiento** (`06 / 01`): TLX-310482-CO por chat. Muestra CRIT-005 y un handoff
   ALTA hacia Operaciones. Luego, en la bandeja del asesor (`06 / 04–08`): detalle con historial
   sin PII, asignar, intentar una transición inválida (422) y cerrar.
6. **PQRS** (`07 / 04–05`): "Me llegó la caja rota" genera un ticket con plazo de 3 días hábiles,
   el checklist y CRIT-003.
7. **DEMO05 Omnicanal** (`09`): el mismo núcleo atiende WhatsApp **SIMULATED** (con botones ≤ 3)
   y PBX **SIMULATED**, donde la guía dictada "T L X nueve ocho…" se reconoce y `speech_text` sale
   sin dígitos.
8. **Seguridad** (`10`): token ausente o incorrecto, intento de SQLi, prompt injection y
   exfiltración de PII.

### Qué decir con honestidad

- WhatsApp y PBX son simulaciones contractuales: no hay envío real a Meta ni a una PBX.
- La confianza es una heurística, no una probabilidad.
- Las tarifas y los recargos que el PDF no define siguen la fórmula `provisional-v1`, con sus
  supuestos visibles en la respuesta.
- No se hicieron pruebas de carga: no se afirma la capacidad de 18.000 interacciones diarias.

---

## Anexo: cambios hechos en esta ventana y motivo

| Archivo | Cambio | Motivo |
|---|---|---|
| `.env` | Rellenado desde `.env.example` (valores locales, tokens de desarrollo aleatorios) | Era una copia con marcadores y la app no arrancaba |
| `backend/app/shared/config.py` | `env_ignore_empty=True` | "vacío = por defecto", como indica `.env.example` |
| `backend/app/main.py` (nuevo) | App real con las 18 operaciones del contrato y adaptadores WhatsApp/PBX simulados | No existía servidor: las rutas eran solo especificación |
| `backend/app/modules/conversation/service.py` (nuevo) | Sesiones, TTL, idempotencia, rate limit, persistencia y auditoría | Núcleo conversacional requerido por los canales |
| `backend/app/modules/conversation/orchestrator.py` | Pre-chequeo CRIT-002, adjuntos en sesión, `llm_used` real, ocultar la respuesta si hay CRIT-001 | Alinear con los ejemplos del contrato (`chat.*.json`) |
| `backend/app/modules/escalation/accounts.py` (nuevo) | Directorio de cuentas desde el fixture SIMULADO | CRIT-004 necesita el tipo de cuenta |
| `backend/app/modules/pqrs/service.py` | Cuenta del remitente en `EscalationInput` | CRIT-004 se omitía en PQRS |
| `backend/app/db/repositories.py` | `flush` del documento antes de sus chunks; `DuplicateMessageError` solo ante UNIQUE (23505) | Bugs reales: FK violada en el reindex; un error de FK se reportaba como duplicado |
| `scripts/bruno_collection.py`, `bruno/translogix-ai/` (nuevos) | Colección Bruno generada desde una especificación, más un runner | Entregable P2 y evidencia reproducible |
| `docs/sql/pgadmin_inspection.sql` (nuevo) | Consultas de solo lectura | Entregable P5 |
