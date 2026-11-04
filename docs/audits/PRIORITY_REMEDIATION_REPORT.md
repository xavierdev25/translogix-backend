# Informe de remediación prioritaria — TransLogix AI (contrato 2.0.0, CR-010)

| | |
|---|---|
| Fecha | 2026-11-04 |
| Base | Commit `dfb05d3` (contrato 1.7.0, RBAC ya implementado) |
| Arquitectura | Monolito modular sin cambios: sin microservicios, Redis, CQRS ni motor de permisos |
| Reutilizado | Roles ADMIN, CLIENT y REVIEWER, verificados antes de empezar: 592 pruebas en verde, incluidas las 39 de RBAC |

Toda la evidencia de este documento se ejecutó en esta sesión: suite, migraciones, carga, saturación,
evaluación de IA y sondas. Lo que no se pudo cerrar se declara como pendiente.

## 1. Hallazgos originales y estado actual

| Id | Hallazgo (auditoría) | Estado actual | Evidencia |
|---|---|---|---|
| SEC-01 | Historial accesible solo con el `session_id` | **Corregido en código** (2.0.0) | `tests/security`: 401 sin credencial; 404 ajeno o inexistente; dueño 200; ADMIN 200; REVIEWER solo con caso en su bandeja; API key 403. Sonda SEC-01: FAIL → PASS (401) |
| SEC-06 | `POST /attachments` sin autenticación ni límite | **Corregido en código** (2.0.0) | Exige `X-Client-Id` y `session_id` del mismo usuario. Otra sesión → 404; 3.ª subida con límite de 2 → 429; PDF incompleto → 422; un adjunto ajeno no se puede referenciar en el chat (404); auditoría `attachment.upload`. Sonda SEC-06: 25/25 aceptadas → 0/25 |
| PII-01 | PII en claro en `shipment_recipients` y `messages.content_json` | **Cifrado implementado y probado. Bloqueado para producción** por la clave de producción y el plazo legal de retención | Ver §5 |
| BIZ-01 / Q1 | Mercancía peligrosa aceptada sin revisión | **Corregido** | Nace `PENDIENTE`; solo un REVIEWER la decide (CLIENT → 403); se registran quién, cuándo y por qué; `can_advance()` bloquea `PENDIENTE` y `RECHAZADA`; el chat (y el LLM) no cambian el estado |
| CAP-01 | Capacidad del LLM sin demostrar | **Comportamiento bajo saturación controlado; la capacidad sigue sin demostrarse** | Saturación de 24 y 40: 0 errores. Pool agotado → 503 `SERVICIO_SATURADO`. ~0,5 respuestas IA/s en este equipo |
| OPS-01 | *Rate limiter* por proceso | **Corregido** | Limitador en PostgreSQL compartido entre dos instancias de la app (2 → 200, 200; 3.ª en la otra instancia → 429). Si PostgreSQL falla, se degrada al limitador por proceso (probado) |
| LLM-01 | Sin reintento ante un fallo transitorio de Ollama | **Corregido** | `ConnectError` una vez → 2 llamadas y éxito; siempre `ConnectError` → exactamente 2 llamadas y `LLM_NO_DISPONIBLE`; `ReadTimeout` → 1 llamada, sin reintento |

## 2. Cambios implementados

| Área | Cambio |
|---|---|
| **SEC-01** | Nueva dependencia `chat_history_reader()`, que acepta al dueño (`X-Client-Id` → HMAC) o a ADMIN/REVIEWER mediante `authenticate()` (la misma de RBAC, sin duplicarla). REVIEWER solo ve sesiones con un caso PENDING o asignado a él (`HandoffRepository.visible_for_session`). Las lecturas de personal y las denegaciones se auditan (`chat.history.read`) |
| **SEC-06** | Nueva dependencia `web_chat_user()`, que exige `X-Client-Id`. La subida exige un `session_id` de una conversación de web chat del mismo usuario, tiene un límite de 10 por minuto por usuario (compartido) y valida que el PDF tenga `%%EOF`. `AttachmentRepository.owned_by()` impide referenciar adjuntos ajenos en `POST /chat/messages`. Auditoría `attachment.upload` |
| **PII-01** | `app/shared/crypto.py`: Fernet/MultiFernet, prefijo `enc:v1:`, tipos `EncryptedText` y `EncryptedJson`. Rechaza el texto plano y los valores manipulados, y permite rotar claves. La clave sale solo de `PII_ENCRYPTION_KEYS`; sin ella la app no arranca (`PiiKeyError`). `app/privacy/`: purga de conversaciones bajo demanda, borrado del destinatario y rotación de claves |
| **BIZ-01** | `shipments.review_status` y `ReviewStatus`. `initial_review_status()` y `can_advance()` en el dominio. La decisión del REVIEWER cambia el estado en la misma transacción; es la única vía después de la creación. `estado_revision` en los resultados de lotes |
| **CAP-01** | Pool configurable (`db_pool_size`, `db_max_overflow`, `db_pool_timeout_seconds=10`). Un `TimeoutError` del pool responde 503 `SERVICIO_SATURADO` con `Retry-After: 5`. Se mantienen la admisión al LLM (4 + 8) y el pool RAG propio de REL-01 |
| **OPS-01** | `app/shared/ratelimit.py`: `PostgresRateLimiter` (ventana de 1 minuto, `INSERT … ON CONFLICT … RETURNING`, pool propio de 2 + 3 conexiones con *timeout* de 2 s, degradación a limitador local). Se usa para el chat, los lotes y los adjuntos. `RATE_LIMIT_BACKEND=memory` deja el comportamiento anterior |
| **LLM-01** | `_OllamaClient.post`: un reintento a los 0,25 s, solo ante `ConnectError` y dentro del *slot* de admisión |

## 3. Archivos y migraciones

**Código nuevo:**
- `app/shared/crypto.py`
- `app/shared/ratelimit.py`
- `app/privacy/{__init__,service,cli}.py`

**Código modificado:**

| Área | Archivos |
|---|---|
| Entrada HTTP | `app/main.py` (historial, adjuntos, referencias de adjuntos y contenedor); `app/auth/dependencies.py`; `app/auth/routes.py` (la decisión exige PENDIENTE) |
| Persistencia | `app/db/models.py` (`EncryptedJson`, `RateLimitCounterRow`); `app/db/repositories.py`; `app/db/session.py` (opciones del pool) |
| Servicios compartidos | `app/shared/{config,http,llm}.py` |
| Envíos | `app/shipments/{domain/models,infrastructure/tables,infrastructure/repositories}.py` |
| Lotes | `app/batches/{api/routes,application/service,infrastructure/repositories}.py` |
| Conversación, adjuntos y auditoría | `app/modules/conversation/service.py`; `app/modules/pqrs/service.py`; `app/audit/domain/models.py` |
| Contrato | `app/contracts/{common,api_spec,openapi,env,shipment_batches}.py` |

**Contrato:**
- `contracts/openapi.yaml` (2.0.0), `schemas/*`, `mocks/manifest.json` y `types/api.d.ts`.
- Ejemplos nuevos o modificados:
  - nuevo: `error.servicio-saturado`;
  - `shipment-batch.response` y `shipment-batch.items`, con `estado_revision`.
- `.env.example` (`PII_ENCRYPTION_KEYS`).
- `pyproject.toml` y `uv.lock`: dependencia `cryptography==50.0.2`.

**Documentación:**
- `contracts/CHANGELOG.md` (2.0.0), `contracts/DECISIONS.md` §10, `docs/contract-change-requests.md`
  (CR-010; CR-007 pasa a 2.1.0/0008);
- `docs/FRONTEND_CAMBIOS_2_0_0.md` (nueva), `docs/FRONTEND_USUARIOS_Y_ROLES.md`, `docs/RBAC.md` y
  `docs/RESPUESTA_FRONT_CRM.md`.

**Migraciones:**

| Migración | Contenido | Verificación sobre `translogix_test` |
|---|---|---|
| `0006_review_gate_ratelimit` | `shipments.review_status` con *backfill*: peligrosa → PENDIENTE; con decisión → esa decisión. Tabla `rate_limit_counters` | Ida y vuelta 0007 → 0005 → 0007 con datos creados por la app: RT-1 `NO_REQUERIDA`, RT-DG `APROBADA` (recuperada de `shipment_reviews`), RT-DG2 `PENDIENTE` |
| `0007_pii_encryption` | Columnas del destinatario a TEXT y cifradas; `messages.content_json` → `{"enc": …}`. Falla si hay datos y no hay clave | 0007: 3 destinatarios con `enc:v1:` y 0 en claro; 2 mensajes cifrados. Downgrade a 0005: los 3 en claro con su longitud original y los mensajes descifrados. Upgrade otra vez: cifrados. `alembic check` sin diferencias |

**Pruebas nuevas o adaptadas:**
- `tests/security/test_remediation.py`: 16 pruebas nuevas.
- Pruebas adaptadas:
  - `tests/auth/test_rbac.py`: la guarda de denegación por defecto cuenta las rutas protegidas por
    dueño;
  - `tests/shipments/test_batches_api.py`: la tabla de destinatarios ya solo guarda texto cifrado;
  - `tests/contracts/test_openapi.py`: seguridad del historial y de los adjuntos;
  - `tests/conftest.py`: clave fija de prueba, pública, solo para la BD de test;
  - `tests/test_llm_admission.py`.

## 4. Pruebas ejecutadas (resultados reales)

| Verificación | Resultado |
|---|---|
| `pytest` completo | **610 aprobadas**, 0 fallidas (antes 592) |
| Cobertura total | **81 %** (antes 80 %). `crypto` 90 %, `ratelimit` 89 %, `auth/dependencies` 99 %, `privacy/service` 76 %. **`privacy/cli` 0 %**: el *wrapper* CLI no tiene prueba propia |
| Autorización por rol | `tests/auth`: 39 aprobadas |
| Aislamiento entre clientes | Lote ajeno → 404 (`tests/auth`) |
| Acceso a adjuntos | Ver SEC-06 en §1 (`tests/security`) |
| Mercancía peligrosa | Ver BIZ-01 en §1 (`tests/security` y `tests/auth`) |
| Concurrencia | Bloqueo por turno (`tests/conversation`, 2) y sonda CON-01 PASS; *rate limit* compartido entre dos instancias; saturación de 24 y 40 sin errores |
| Regresión de rastreo, cotización, escalamiento y degradación sin LLM | `tests/conversation`: 8 aprobadas, incluida la fórmula oficial (190.975), CRIT-002 y CRIT-005 |
| RAG (golden set, Ollama real) | **18/18**, 13/13 responde, 5/5 se abstiene, **0 alucinaciones**; p50 2.276 ms |
| Sondas | **23 PASS / 3 FAIL / 1 BLOCKED** (antes 21/5/1). Ver el detalle debajo |
| `mypy app tests` (estricto) | Limpio (169 archivos) |
| `ruff check .` | 19 E501 heredados (`orchestrator.py`, `intents.py`); ninguno nuevo |
| Contrato OpenAPI | `python -m app.contracts --check`: al día (2.0.0). Tipos TS regenerados (`openapi.sha256` = `b115e8ce…f437`) |
| Migraciones | `alembic check` limpio; ida y vuelta con datos (§3) |

**Sondas que siguen fallando o bloqueadas:**
- SEC-03: `/docs` público; fuera del alcance de este encargo.
- RAG-02 y TRK-02: errores conocidos de la propia sonda.
- HITL-06: faltan datos de cuentas CORPORATIVO_GOLD.

**Rendimiento** (mismo equipo y método que en la auditoría, `tests/load/run_load.py`; limitador
compartido **activo** y cifrado activo):

| Escenario | 1.6.0: req/s, p50 / p95 / p99 (ms) | 2.0.0: req/s, p50 / p95 / p99 (ms) | Errores |
|---|---|---|---|
| Chat de rastreo c = 1 | 210 · 4,7 / 5,3 / 6,3 | 176 · 5,6 / 6,6 / 9,0 | 0 |
| Chat de rastreo c = 20 | 415 · 45,6 / 65,9 / 76,8 | 349 · 54,7 / 76,0 / 86,9 | 0 |
| Chat de rastreo c = 50 | 400 · 82,2 / 353,8 / 519,5 | 342 · 134,8 / 408,1 / 582,3 | 0 |
| Tracking c = 50 | 1.098 · 33,5 / 123,4 / 177,5 | 905 · 41,7 / 145,3 / 227,7 | 0 |
| `POST /cotizar` c = 50 | 473 · 59,2 / 335,7 / 529,1 | 351 · 87,1 / 421,6 / 716,2 | 0 |
| Chat RAG c = 1 | 0,5 · 1.881 / 1.969 | 0,4 · 2.107 / 6.168 (un valor atípico tras el arranque) | 0 |
| Chat RAG c = 4 | 0,5 · 7.385 / 7.464 | 0,5 · 8.316 / 8.364 | 0 |
| Saturación de 24 RAG | 0 errores, p50 1.583 ms | **0 errores**, p50 2.545 ms; 12 citadas y 12 degradadas | 0 |
| Saturación de 40 RAG | 0 errores | **0 errores**, p50 1.060 ms; 12 citadas y 28 degradadas | 0 |

**Lectura:**
- El turno de chat cuesta entre un 15 y un 20 % más de rendimiento: hay una ida y vuelta al limitador
  compartido y cifrado y descifrado de mensajes. La auditoría ya registró que este portátil varía
  hasta 3× entre ejecuciones (`/cotizaciones`: 261–911 req/s), así que la cifra es orientativa.
- **Ollama:** 4 en paralelo tardan unas 4 veces más; el rendimiento total sigue en ~0,5 respuestas/s.
  `OLLAMA_NUM_PARALLEL` no está definido (valor por defecto).
- **No se afirma soporte para 18.000 interacciones/día:** las pruebas duran segundos, se hacen en un
  portátil y no reproducen la mezcla real.

## 5. Riesgos pendientes

| Riesgo | Detalle | Qué falta |
|---|---|---|
| **Clave de producción** | Las claves de dev y test son locales: la de test es pública y está en `conftest`; la de dev, si se despliega, va en `.env`, que no está versionado | Gestor de secretos, procedimiento de respaldo de la clave (sin clave los datos son irrecuperables) y política de rotación (`app.privacy.cli rotate-keys` ya existe) |
| **Retención** | El mecanismo existe (purga y borrado bajo demanda); **el plazo es una decisión legal pendiente** (Ley 1581). Nada se borra automáticamente (`conversation_retention_days = None`) | El plazo aprobado y una tarea programada |
| **PII que este cambio no cifra** | `handoffs.payload_json` (lleva el historial de la conversación), `pqrs_tickets.payload_json` y los **archivos adjuntos en disco** (`var/attachments`: facturas, cédulas) | Extender `EncryptedJson` a esas dos tablas y cifrar los archivos. Se dejó fuera porque el encargo nombraba solo dos tablas |
| **Índice ciego** | No se implementó: hoy ninguna búsqueda usa esos campos | Añadirlo con CR-007 si el CRM busca por teléfono o documento |
| **Despacho** | No existe una transición operativa (solo `REGISTRADO`, DEC-SHP5). `can_advance()` está listo, pero no hay quién lo llame | CR-005: la transición debe usar `can_advance()` |
| **Capacidad (CAP-01)** | ~0,5 respuestas IA/s con un solo Ollama; por encima de 12 simultáneas, las demás se degradan | Infraestructura de inferencia (Q10) y una prueba sostenida con la mezcla real |
| **Ventana fija del limitador** | En el borde entre dos ventanas pueden pasar hasta 2× el límite | Aceptado por simplicidad |
| **Otros pendientes** | `/docs` público (SEC-03, Q4); CRIT-004 sin datos (Q7); inactividad (Q8); modos de CRIT-003/005 (Q6) | Decisiones de negocio, sin cambios en esta entrega |
| **`:8000` sigue en 1.7.0** | El código 2.0.0 no está desplegado en el túnel, porque romperíamos al front | Ver §6 y §8 |

## 6. Cambios necesarios en el frontend

Detalle en [`FRONTEND_CAMBIOS_2_0_0.md`](../FRONTEND_CAMBIOS_2_0_0.md).

1. **Historial:** enviar `X-Client-Id` (el mismo `client_id` del chat). Sin él → 401.
2. **Adjuntos:**
   - enviar `X-Client-Id` y el campo `session_id`, lo que exige haber enviado antes un mensaje;
   - tratar 401, 404, 422 y 429.
3. **Mostrar `estado_revision`** en el panel de lotes del cliente y en el de mercancía peligrosa.
4. **Tratar el 503 `SERVICIO_SATURADO`** como reintentable (`Retry-After`).

**Sin cambios:** CORS (ya admitía `X-Client-Id`), el chat, el rastreo, la cotización, los roles y
las cuentas de demo.

## 7. Compatibilidad contractual

- **Versión: 2.0.0 (MAJOR).** Dos validaciones se endurecen:
  - `X-Client-Id` en el historial;
  - `X-Client-Id` y `session_id` en los adjuntos.
- **Aditivos:**
  - el esquema `ClientId`;
  - `estado_revision` y `ReviewStatus`;
  - `SERVICIO_SATURADO`;
  - `PII_ENCRYPTION_KEYS`.
- **Coordinación de CRs:**
  - CR-008 (1.6.0) y CR-009 (1.7.0) siguen vigentes;
  - **CR-007 pasa a 2.1.0 / migración 0008.**
- **Registrado en** CHANGELOG, CR-010 y DECISIONS §10.

## 8. Estado de cada bloqueo de producción

| Bloqueo (PRODUCTION_READINESS) | Antes | Ahora |
|---|---|---|
| P1 PII en claro | Abierto | **Mitigado en código.** Abierto hasta tener la clave de producción, cifrar `handoffs`, `pqrs` y los adjuntos en disco, y desplegar |
| P2 Retención | Abierto | **Mecanismo listo; bloqueado por la decisión legal** del plazo |
| P3 Adjuntos | Abierto | **Cerrado en código** (falta desplegar 2.0.0) |
| P4 Historial | Abierto | **Cerrado en código** (falta desplegar 2.0.0) |
| P5 Mercancía peligrosa | Abierto | **Cerrado**. Pendiente: que la futura transición de despacho llame a `can_advance()` |
| P6 Capacidad | Abierto | **Abierto.** El comportamiento es controlado (0 errores y 503 en lugar de 500), pero la capacidad no está demostrada |
| P7 Instancia única y canales simulados | Abierto | **Limitador compartido cerrado**; WhatsApp y PBX siguen en `SIMULATED` |
| P8 `/docs` público | Abierto | Abierto (Q4, fuera de alcance) |

**Producción sigue bloqueada por:** la clave de producción, el plazo de retención, la PII restante,
la capacidad, los canales reales y `/docs`.

**Despliegue de 2.0.0 en el entorno de desarrollo (`:8000`)**, pendiente de aviso al front:
1. generar una clave de dev en `.env` (`uv run python -m app.privacy.cli generate-key`);
2. `alembic upgrade head` (0005 → 0007, cifra los datos existentes);
3. reiniciar.

## 9. Indicadores de la rúbrica afectados

No se suben puntuaciones sin evidencia; la escala es la de `RUBRIC_EVALUATION.md`.

| Indicador | Antes | Ahora | Por qué |
|---|---|---|---|
| 1.1 Funcionamiento general | 2 | 2 | Más robusto (adjuntos, historial y saturación con 503), pero 2.0.0 no está desplegado y el canal asesor→cliente sigue pendiente (CR-007) |
| 1.4 Velocidad de IA | 2 | 2 | Mismo comportamiento del RAG (~0,5/s, degradación inmediata). El chat determinista es entre un 15 y un 20 % más lento por el limitador y el cifrado; sigue en ms |
| 1.5 Precisión de IA | 2 | 2 | Golden set 18/18 sin alucinaciones, mantenido; el conjunto sigue siendo pequeño |
| 2.2 Reglas de negocio | 2 | 2 (mejora parcial) | Q1 resuelta (revisión de mercancía peligrosa). Siguen abiertas Q6, Q7 y Q8 |
| 2.4 Utilidad real | 2 | 2 | Sin cambios funcionales visibles para el usuario final |
| 2.5 Potencial de crecimiento | 2 | 2 (mejora parcial) | Limitador compartido entre instancias y cifrado con rotación. Siguen un solo Ollama y la PII restante |
| Resto (1.2, 1.3, 2.1, 2.3, 3.x) | N/O | N/O | No observables desde el backend |
