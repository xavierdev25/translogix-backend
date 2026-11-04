# TransLogix AI: Integration Brief

Síntesis de `FRONTEND_HANDOFF.md`, `BACKEND_CURRENT_STATE.md`, `FRONTEND_INTEGRATION_EXAMPLES.md` y
`openapi.json`, cuyo OpenAPI sigue siendo idéntico al de la app en ejecución. El detalle campo a
campo está en `FRONTEND_HANDOFF.md`.

## 1. Arquitectura actual

| Componente | Tecnología | Responsabilidad |
|---|---|---|
| API | Python 3.12, FastAPI, Pydantic v2; monolito modular | 18 operaciones HTTP, validación, sobre de errores, CORS, `X-Request-ID` |
| Base de datos | PostgreSQL + pgvector 0.8.6, SQLAlchemy async, Alembic (migración `0001`) | Sesiones, mensajes, handoffs, cotizaciones, PQRS, adjuntos, auditoría e índice RAG con `vector(1024)` |
| IA | Ollama: `qwen3:8b` (chat) y `qwen3-embedding:0.6b` | Redactar respuestas RAG y clasificar intenciones ambiguas. Cálculos, plazos y escalamiento son deterministas |
| Datos | Archivos en `data/` | TMS **simulado** (5 guías), tarifas CSV, cobertura, manuales, reglas de escalamiento y fixture de cuentas simuladas |
| Contratos | `backend/app/contracts/` (v1.2.0) | Fuente de verdad de los modelos y del OpenAPI |

## 2. Frontend

| Ítem | Estado |
|---|---|
| Framework | NO DOCUMENTADO |
| Estructura | NO DOCUMENTADO. El repositorio no contiene frontend |
| Estado actual | NO DOCUMENTADO. La etapa documentada era solo de backend |
| Funcionalidades existentes | NO DOCUMENTADO |
| Único dato relacionado | El CORS local permite `http://localhost:5173` (`CORS_ALLOW_ORIGINS`) |

## 3. Backend: módulos y estado

| Módulo | Estado | Funcionalidad |
|---|---|---|
| Sesiones de conversación | PROBADO | Crea o reutiliza una sesión por `client_id` (TTL de 30 min); idempotencia por `message_id`; 404 ante sesión ajena |
| Orquestador IA | PROBADO | Reglas primero y LLM como último recurso; cotización por turnos; guardia contra prompt injection |
| Tracking | PROBADO | Vista sin PII sobre un TMS simulado |
| Cotización | PROBADO | Fórmula `provisional-v1`, sin tarifas inventadas |
| RAG | PROBADO | Búsqueda híbrida pgvector + BM25 sobre 84 chunks; citas; sin evidencia, escala por CRIT-001 |
| Escalamiento | PROBADO | CRIT-001, 002, 003 y 005. CRIT-004 no puede dispararse con el fixture actual |
| Handoffs | PROBADO | Bandeja con transiciones PENDING → ASSIGNED → CLOSED |
| PQRS | PROBADO | Elegibilidad, checklist, liquidación preliminar y escalamiento |
| Adjuntos | PROBADO (PDF) | Tipo detectado por contenido, máx. 10 MB. JPEG sin prueba |
| Adjuntos en el chat | IMPLEMENTADO | Sin prueba de extremo a extremo |
| Métricas por canal | PROBADO | Agregados por canal |
| WhatsApp | SIMULADO | `/simulate` probado. Webhook real implementado pero sin probar (no hay credenciales). Sin envío a Meta |
| PBX | SIMULADO | Solo transcripción; `audio_ref` → 503 (no hay ASR) |
| Caída del LLM y rate limit | IMPLEMENTADO | No probados en la auditoría |

## 4. API

Base local: `http://localhost:8000`. Prefijo `/api/v1`, salvo `/health` y `/ready`.

| Módulo | Método | Ruta | Propósito |
|---|---|---|---|
| Operación | GET | `/health` | Proceso vivo |
| Operación | GET | `/ready` | BD, Ollama e índice RAG listos (503 si alguno falla) |
| Tracking | GET | `/api/v1/tracking/{guia}` | Estado seguro de una guía |
| Cotización | POST | `/api/v1/cotizar` | Calcular el flete |
| PQRS | POST | `/api/v1/pqrs/crear-ticket` | Crear un ticket |
| Adjuntos | POST | `/api/v1/attachments` | Subir un PDF o JPEG (multipart) |
| Chat | POST | `/api/v1/chat/messages` | Enviar un mensaje y recibir la respuesta |
| Chat | GET | `/api/v1/chat/sessions/{session_id}/history` | Historial de la sesión |
| WhatsApp | GET | `/api/v1/channels/whatsapp/webhook` | Verificación del webhook (Meta) |
| WhatsApp | POST | `/api/v1/channels/whatsapp/webhook` | Webhook firmado con HMAC |
| WhatsApp | POST | `/api/v1/channels/whatsapp/simulate` | Simulación |
| PBX | POST | `/api/v1/channels/pbx/turns` | Turno de llamada |
| PBX | POST | `/api/v1/channels/pbx/simulate` | Simulación de un turno |
| Handoff | GET | `/api/v1/handoffs` | Bandeja (filtro `status`) |
| Handoff | GET | `/api/v1/handoffs/{handoff_id}` | Detalle con historial sin PII |
| Handoff | PATCH | `/api/v1/handoffs/{handoff_id}` | Asignar o cerrar |
| Métricas | GET | `/api/v1/metrics/channels` | Conteos por canal |
| RAG (admin) | POST | `/api/v1/admin/rag/reindex` | Reindexar el corpus |

No existen: WebSocket, mensaje del asesor al cliente, login, consulta RAG directa, ni consulta de
tickets o cotizaciones por id.

## 5. Contratos mínimos (valores reales)

**Chat: primer mensaje.** Se envía sin `session_id`; la sesión la crea el backend.

```json
{"client_id": "ca85aa16-726d-41d4-b466-b539344d7e56", "message_id": "6ac9ecf4-42fa-4593-9a5a-56b7c6a0cc57", "text": "Hola"}
```

La respuesta es `{"reply": ConversationReply}`, con estos campos clave: `session_id`, `reply_id`,
`status` (`AWAITING_USER` o `ESCALATED`), `blocks[]` y `meta`. Hay ocho tipos de bloque:
`TEXT`, `QUICK_REPLIES`, `TRACKING_CARD`, `QUOTE_SUMMARY`, `CITATIONS`, `REQUEST_ATTACHMENTS`,
`PQRS_TICKET` y `HANDOFF_NOTICE`.

**Escalamiento** (al escribir "TLX-310482-CO" en el chat):

```json
{"type": "HANDOFF_NOTICE", "handoff_id": "6c72e632-…", "prioridad": "URGENTE", "cola": "OPERACIONES", "tiempo_espera_estimado_segundos": 45}
```

La respuesta trae además `"status": "ESCALATED"`.

**Tracking** (`GET /api/v1/tracking/TLX-984021-CO`, fragmento):

```json
{"numero_guia": "TLX-984021-CO", "estado_actual": {"codigo": "EN_TRANSITO", "etiqueta": "En tránsito"}, "fecha_estimada_entrega": "2026-11-03T16:00:00-05:00"}
```

**Cotización:**

```json
{"origen": "Bogotá D.C.", "destino": "Medellín", "alto_cm": 40, "ancho_cm": 50, "largo_cm": 60, "peso_real_kg": 45.5, "valor_declarado_cop": 12500000}
```

Respuesta (fragmento):

```json
{"calculo": {"peso_facturable_kg": 45.5, "flete_base_cop": 65975, "seguro_cop": 125000, "flete_total_cop": 190975}, "tiempo_entrega_horas": 24, "formula_version": "provisional-v1"}
```

**Handoff (bandeja):** `PATCH /api/v1/handoffs/{id}` con `{"status": "ASSIGNED"}` o
`{"status": "CLOSED"}`.

**Error** (formato único):

```json
{"error": {"code": "GUIA_NO_ENCONTRADA", "message": "No encontramos una guía con ese número. Verifica que esté bien escrita.", "details": {"numero_guia": "TLX-000000-CO"}, "request_id": "6ca49e60-…"}}
```

Programa la UI contra `error.code`, no contra `message`.

## 6. Autenticación

| Ámbito | Mecanismo | Header |
|---|---|---|
| Cliente final (chat) | **Sin login.** Lo identifica un `client_id` (UUID v4) que genera la UI | — |
| Historial | Verificación de dueño, **opcional** | `X-Client-Id: <client_id>` |
| Bandeja de handoffs y reindex | Un único token estático compartido; sin él o con uno incorrecto → 403 | `X-Admin-Token` |
| Webhook WhatsApp | Firma HMAC-SHA256 | `X-Hub-Signature-256` |
| Usuarios y roles de asesor | **No existen** | — |

El token de administración no puede ir en un bundle público. CORS solo admite los orígenes de
`CORS_ALLOW_ORIGINS`, con los métodos GET, POST y PATCH, y sin cookies.

## 7. Orden recomendado de integración

1. **Base:** configurar CORS con el origen de la UI y comprobar `/health` y `/ready`.
2. **Tracking y cotización directos:** sin estado, sin BD de sesión y deterministas.
3. **Chat:**
   - generar y guardar `client_id`;
   - enviar mensajes;
   - guardar `reply.session_id` **en cada respuesta**;
   - renderizar los bloques en orden.
4. **Robustez del chat:**
   - reintentar con el mismo `message_id`;
   - ante 404 `NO_ENCONTRADO`, descartar `session_id` y reenviar;
   - ante 429, esperar unos 60 s;
   - rehidratar con el historial enviando `X-Client-Id`.
5. **Escalamiento en la UI:** banner con `HANDOFF_NOTICE` y estado `ESCALATED`.
6. **Adjuntos y PQRS:** subir el archivo y referenciar `attachment_id`.
7. **Bandeja del asesor:** en un entorno interno o BFF, con el token y consultas periódicas.
8. **Métricas y canales simulados:** para la demo omnicanal.

## 8. Pendientes y riesgos

| Severidad | Hallazgo |
|---|---|
| Alta (bloqueante) | No hay canal asesor→cliente ni push. El cliente recibe "Te responderá en este mismo chat", pero no existe mecanismo para hacerlo |
| Alta | Web Chat no acepta `interactive_reply_id`. Enviados como texto, fallan los botones `novedad`, `otra_guia` y `cadena_frio`; `equipo_legal` solo funciona con el LLM |
| Media | El historial es legible con solo el `session_id` si se omite `X-Client-Id` |
| Media | Cualquier PQRS, incluso una petición, sobre una guía declarada en más de $5M escala a `JURIDICO_PQRS` |
| Media | Un solo token de administración, sin identidad de asesor |
| Media | Sin pruebas pytest para el orquestador, el servicio de conversación ni las rutas (solo 385 pruebas de contratos y pruebas de extremo a extremo) |
| Baja | El texto RAG puede traer Markdown o LaTeX: renderizar como texto plano |
| Baja | La voz del PBX lee los números dígito a dígito |
| Baja | `/docs`, `/openapi.json` y las métricas son públicos |
| Baja | Rate limit en memoria, por proceso |
| Baja | El navegador no puede leer `X-Request-ID` (no hay `expose_headers`): usar `error.request_id` |
| Info | Datos simulados; sin pruebas de carga; sin control de versiones; Docker no verificado; estado `IDLE` y WebSocket pendientes |

## 9. Ejecución

**Backend** (requiere PostgreSQL con pgvector y Ollama con los dos modelos):

```bash
cd ~/Desktop/translogix-ai
cp -n .env.example .env
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Después de `cp`, completa `.env`. La primera vez, indexa el corpus RAG:

```bash
TOKEN=$(grep '^ADMIN_TOKEN=' ../.env | cut -d= -f2-)
curl -s -X POST localhost:8000/api/v1/admin/rag/reindex -H "X-Admin-Token: $TOKEN" -H 'Content-Type: application/json' -d '{}'
```

**Base de datos:** el comando para levantar PostgreSQL está NO DOCUMENTADO. La documentación solo
exige un `DATABASE_URL` hacia PostgreSQL con pgvector; las tablas las crea
`uv run alembic upgrade head`.

**Frontend:** NO DOCUMENTADO.
