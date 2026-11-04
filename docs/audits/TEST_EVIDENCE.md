# Evidencia de pruebas — auditoría del 2026-11-04

Este documento registra cada comando, sus datos y su resultado, antes y después de las correcciones.

**Reglas:**
- Todo se ejecutó contra la base de datos aislada `translogix_test`, nunca contra la de desarrollo ni
  contra producción.
- Ningún token, clave ni PII aparece en este documento. El token de administración de las sondas se
  generó al azar y se guardó fuera del repositorio con permisos 600.

## 1. Entorno

| Elemento | Valor |
|---|---|
| Equipo | Apple M5 Pro, 18 núcleos, 24 GiB de RAM, macOS 27.2 (portátil de desarrollo) |
| Base de datos | PostgreSQL 18.6 (Postgres.app) + pgvector 0.8.6, `max_connections=100`, local (`:54329`) |
| IA | Ollama 0.40.14 local: `qwen3:8b` (`think:false`, temperatura 0) y `qwen3-embedding:0.6b` (1024) |
| API | uvicorn con **1 proceso** y log `warning`. Pool SQLAlchemy por defecto (5 + 10 de desborde) |
| Servidor de auditoría | `:8010` contra `translogix_test`, con `CHAT_RATE_LIMIT=0` y `BATCH_RATE_LIMIT=0` para medir capacidad y no el limitador. `:8011`, la misma configuración con Ollama inalcanzable |
| Datos | Los *fixtures* oficiales: 5 guías TMS, `matriz_tarifas_y_fletes.csv`, el directorio de cobertura y el corpus de 4 documentos (84 *chunks*). No se añadió ni se modificó ningún dato |

## 2. Verificación estática y suite

| Comando | Antes | Después |
|---|---|---|
| `uv run pytest` (unitarias + contrato + integración PostgreSQL) | 515 aprobadas | **536 aprobadas**, 0 fallidas |
| Cobertura (`--cov=app`) | 73 % | **80 %** |
| `uv run ruff check .` | 20 (todos E501 heredados) | **19** (E501 heredados en `orchestrator.py` e `intents.py`) |
| `uv run mypy app tests` (estricto) | limpio | limpio (154 archivos) |
| `alembic check` (base de test) | sin operaciones | sin operaciones (no hay cambios de esquema) |
| `python -m app.contracts --check` (OpenAPI, esquemas, ejemplos, *mocks*) | al día (1.5.0) | al día (**1.6.0**) |
| `contracts/types/generate.sh` | — | tipos regenerados (`openapi.sha256` = `b98636cd…afd7`) |

**Pruebas nuevas (21):**

| Archivo | Qué cubre |
|---|---|
| `tests/test_http_body_limit.py` (8) | Límite declarado y en *streaming*, *multipart*, límite por prefijo, ruta exenta, y la app real con CORS y `X-Request-ID` |
| `tests/test_llm_admission.py` (3) | Rechazo inmediato por encima de la cola, liberación del *slot* tras un fallo, pool RAG separado |
| `tests/conversation/test_chat_flows.py` (6) | Con Ollama desactivado y la base de test: rastreo, fórmula oficial, CRIT-002, CRIT-005, degradación sin LLM, texto de espera del asesor |
| `tests/conversation/test_conversation_lock.py` (2) | Bloqueo de fila: una lectura con bloqueo espera, una sin bloqueo no |
| Pruebas de contrato parametrizadas (+2) | El código nuevo y su ejemplo |

**Prueba descartada (registro honesto):** una prueba de dos turnos simultáneos por HTTP en proceso
pasaba también **sin** la corrección. Con el bloqueo desactivado a mano, tres ejecuciones seguidas
pasaron: el cliente ASGI en proceso serializa las dos peticiones (respuestas a los 6,9 y 13,7 ms). Se
sustituyó por la prueba de bloqueo con dos sesiones de base de datos. El caso de punta a punta se
verifica contra el servidor real (sonda CON-01, §4).

## 3. Evaluación de la IA (`tests/ai_eval/`)

```bash
uv run python tests/ai_eval/run_eval.py http://127.0.0.1:8010 --json out.json
```

**Conjunto:** 18 preguntas a través del chat, cada una con un cliente nuevo.
- **13 ANSWER:** deben responder con cita y contener una palabra clave. Salen de la lista de
  prohibidos, la MSDS, el IMO y las clases ONU de la especificación oficial, y del protocolo de PQRS.
- **5 NO_ANSWER:** contenedor a Hong Kong, mundial de fútbol, Venezuela, dólar y criptomonedas. Si
  responden con una afirmación, es una alucinación.

**Juez:** ACIERTO / FALLO / ESCALA / ALUCINA. Sale con código 1 si hay alguna alucinación.

| Ejecución | Aciertos | Responde | Se abstiene | Alucinaciones | Latencia p50 / máx. |
|---|---|---|---|---|---|
| Línea base (código 1.5.0) | 14/18 | 9/13 | 5/5 | **0** | 1.677 / 3.326 ms |
| Tras la corrección AI-01, palabras clave originales | 17/18 | 12/13 | 5/5 | **0** | 2.156 / 3.643 ms |
| Tras la corrección AI-01, palabras clave revisadas | 18/18 | 13/13 | 5/5 | **0** | 1.547 / 3.350 ms |
| Final, con todas las correcciones (1.6.0) | **18/18** | **13/13** | **5/5** | **0** | 1.860 / 3.419 ms |

**Fallos de la línea base:**

| Caso | Pregunta | Resultado | Causa |
|---|---|---|---|
| G04 | Efectivo | ESCALA por CRIT-001, confianza 0,743 | Defecto real (AI-01) |
| G05 | Pólvora | ESCALA por CRIT-001, confianza 0,662 | Defecto real (AI-01) |
| G10 | Pintura con solventes | ESCALA por CRIT-001, confianza 0,740 | Defecto real (AI-01) |
| G01 | Ácido clorhídrico | FALLO del juez, confianza 0,883 | Falso negativo del juez: respuesta correcta sin "clase 8" |

**Transparencia sobre las palabras clave.** Dos casos se ampliaron tras revisar a mano la respuesta:

| Caso | Qué dijo la respuesta | Palabras clave ahora |
|---|---|---|
| G01 | Citaba la MSDS y el embalaje de doble pared sin nombrar la clase | `clase 8`, `corrosiv`, `msds`, `doble pared` |
| G04 | "no será receptado ni transportado", citando la lista de prohibidos, sin la palabra "prohibido" | Se añadieron "no será" y "no se transporta" |

- Cada ampliación queda anotada en `golden_set.json` (campo `nota`).
- Con el juez revisado, la línea base sería **15/18**: G01 cuenta como acierto y siguen fallando G04,
  G05 y G10, que eran defectos reales.
- La mejora real atribuible a AI-01 es **de 15/18 a 18/18**.
- El conjunto es pequeño (18). No sustituye una evaluación con preguntas reales de clientes.

## 4. Sondas funcionales y de seguridad

Fuera del repositorio. Comparación de `probes_before.json` con `probes_after.json` (27 sondas):

| | PASS | FAIL | BLOCKED |
|---|---|---|---|
| Antes | 17 | 9 | 1 |
| Después | **21** | 5 | 1 |

**Sondas que cambian:**

| Id | Antes | Después | Observado después |
|---|---|---|---|
| RAG-03 (pólvora o explosivos) | FAIL (escala, confianza 0,65) | PASS | Confianza 0,853; 2 citas; "prohibidos en su totalidad" |
| SEC-04 (JSON de 25 MB) | FAIL: 422 tras leerlo; RSS 76,4 → 181,6 MB | PASS | Ver detalle abajo |
| SEC-05 (subida de 30 MB, límite 10 MB) | FAIL: 422 `VALIDACION` tras recibirla entera | PASS | Ver detalle abajo |
| CON-01 (dos turnos simultáneos en la misma sesión) | FAIL: se perdió un dato y el bot volvió a preguntar | PASS | `QUOTE_SUMMARY`: 45,5 kg, total $190.975 |

**Detalle de SEC-04 y SEC-05 después de la corrección:**
- Con curl: **413 `CUERPO_DEMASIADO_GRANDE`**. curl envía `Expect: 100-continue` y el servidor
  respondió sin leer el cuerpo:

  | Sonda | Bytes subidos | Tiempo | `max_bytes` | RSS |
  |---|---|---|---|---|
  | SEC-04 | 0 | 16 ms | 1.048.576 | 79,0 → 79,1 MB |
  | SEC-05 | 0 | 13 ms | 10.551.296 | sin cambio |

- Con httpx: el cliente envía todo el cuerpo sin esperar respuesta y recibe `ReadError` porque el
  servidor ya cerró la conexión (RSS 82,6 → 82,6 MB). Es el comportamiento esperado de un servidor que
  corta antes de leer. Las sondas se ajustaron para aceptar ese cierre y comprobar además que la memoria
  no crece.

**Sondas que siguen en FAIL o BLOCKED:**

| Id | Estado | Causa |
|---|---|---|
| RAG-02 (efectivo) | FAIL del juez de la sonda | Busca "prohib". La respuesta correcta y citada dice "no será receptado ni transportado" (el mismo falso negativo que G04). **Correcta tras revisión manual** |
| TRK-02 | Error de la sonda | Esperaba `RELECTURA_HUB_FONTIBON` en TLX-984021-CO, pero ninguna guía del *fixture* pasa por ese estado. El mapa `data/config/tms_state_map.json` tiene la traducción literal del enunciado. Ver DEMO-01 |
| SEC-01 | FAIL | Historial sin `X-Client-Id`: 200 con 2 mensajes. Pendiente Q2 |
| SEC-03 | FAIL | `/docs` responde 200. Pendiente Q4 |
| SEC-06 | FAIL | 25 de 25 subidas aceptadas sin límite. Pendiente Q3 |
| HITL-06 | BLOCKED | CRIT-004 sin datos: no hay NIT `CORPORATIVO_GOLD` ni cuentas con más de 500 envíos/mes (DEC-ACC1) |

**Sondas que pasan antes y después:**

| Área | Sondas |
|---|---|
| RAG | RAG-01, RAG-04 a RAG-08 (incluye la exposición del *prompt*) |
| Rastreo | TRK-01 (guía con espacio, formato del PDF) |
| Cotización | COT-01 (fórmula oficial), COT-02 (seguro mínimo 2.500), COT-03 (TEST_ONLY no comercial) |
| Escalamiento | HITL-01 a HITL-05 (CRIT-002 ×2, CRIT-005, CRIT-003, *payload* sin PII) |
| Seguridad | SEC-02 (enumeración), SEC-07 (SQL injection) |

## 5. Rendimiento

```bash
uv run python tests/load/run_load.py http://127.0.0.1:8010 --server-pid <pid> --json out.json
```

**Metodología:**
- Cliente httpx asíncrono en el mismo equipo, con hasta 100 conexiones.
- Cada escenario ejecuta N peticiones con C trabajadores.
- Duración por escenario: de 0,5 a 16 s. La batería completa dura ~1,5 min.
- **No es una prueba sostenida** (no hay *soak* de horas).
- Una sola ejecución por escenario, salvo donde se indica.

**Variabilidad:** `/cotizaciones` con C = 50 se repitió tres veces seguidas y dio **261, 483 y 911
req/s**. En este portátil la variación entre ejecuciones llega a 3×, así que las cifras son órdenes de
magnitud, no límites.

| Escenario | C | N | Antes: req/s | Antes: p50 / p95 / p99 (ms) | Después: req/s | Después: p50 / p95 / p99 (ms) | Errores |
|---|---|---|---|---|---|---|---|
| `GET /health` | 1 | 1.000 | 1.987 | 0,4 / 0,5 / 0,6 | 2.004 | 0,4 / 0,5 / 0,6 | 0 |
| `GET /health` | 50 | 2.000 | 1.116 | 31,9 / 121,7 / 192,6 | 1.104 | 33,0 / 130,3 / 182,3 | 0 |
| `GET /tracking/{guía}` | 1 | 1.000 | 939 | 1,0 / 1,3 / 1,4 | 934 | 1,1 / 1,2 / 1,3 | 0 |
| `GET /tracking/{guía}` | 50 | 2.000 | 1.115 | 33,1 / 120,2 / 186,2 | 1.098 | 33,5 / 123,4 / 177,5 | 0 |
| `POST /cotizar` | 50 | 1.000 | 459 | 78,4 / 297,2 / 452,5 | 473 | 59,2 / 335,7 / 529,1 | 0 |
| `POST /cotizaciones` | 50 | 1.000 | 446 | 79,6 / 312,0 / 468,5 | 276 (repeticiones: 261, 483 y 911) | 125,8 / 537,5 / 752,7 | 0 |
| Chat de rastreo, sin LLM | 1 | 300 | 201 | 4,8 / 6,1 / 8,4 | 210 | 4,7 / 5,3 / 6,3 | 0 |
| Chat de rastreo, sin LLM | 20 | 1.000 | 406 | 46,4 / 68,5 / 83,6 | 415 | 45,6 / 65,9 / 76,8 | 0 |
| Chat de rastreo, sin LLM | 50 | 1.000 | 401 | 48,2 / 359,1 / 595,6 | 400 | 82,2 / 353,8 / 519,5 | 0 |
| Chat RAG + LLM | 1 | 8 | 0,5 | 1.901 / 2.019 / 2.019 | 0,5 | 1.881 / 1.969 / 1.969 | 0 |
| Chat RAG + LLM | 4 | 8 | 0,5 | 7.384 / 7.486 / 7.486 | 0,5 | 7.385 / 7.464 / 7.464 | 0 |

**Latencia directa de Ollama, sin la API:**

| Llamada | N | Antes: p50 / p95 (ms) | Después: p50 / p95 (ms) |
|---|---|---|---|
| `/api/embed` (1 texto) | 20 | 13,0 / 15,4 | 11,0 / 12,5 |
| `/api/chat` (120 tokens) | 6 | 1.237 / 1.356 | 1.178 / 1.260 |

**Lectura:**
- El turno RAG (~1,9 s) son *embedding* (~12 ms), recuperación y una generación (~1,2–1,8 s).
- Con 4 en paralelo, cada respuesta tarda ~4 veces más: Ollama no aumenta el rendimiento total, que
  se queda en **~0,5 respuestas/s** en este equipo.

**Memoria:**

| Corrida | RSS | Condición del servidor |
|---|---|---|
| Antes | 24,6 → 98,5 MB | Recién arrancado |
| Después | 111,5 → 114,1 MB | Ya cargado por sondas, evaluación y saturación |

No se observó crecimiento sostenido en la duración medida.

## 6. Saturación del chat con IA (REL-01)

```bash
uv run python <scratchpad>/rag_saturation.py <N>
```

Lanza N preguntas documentales nuevas a la vez (un cliente nuevo por pregunta).

| Versión | N | Duración | p50 / p95 / máx. (ms) | Resultado |
|---|---|---|---|---|
| 1.5.0 (antes) | 24 | 53,1 s | 37.529 / 51.823 / 53.110 | 14 con citas; **10 × HTTP 500** |
| Solo con la admisión al LLM | 24 | 48,3 s | 31.437 / 46.009 / 48.247 | 12 con citas; 2 degradadas; **10 × HTTP 500** |
| 1.6.0: admisión + pool RAG propio | 24 | 16,3 s | 1.583 / 14.907 / 16.298 | 12 con citas; 12 degradadas; **0 errores** |
| 1.6.0 | 40 | 16,1 s | 1.003 / 13.682 / 16.085 | 12 con citas; 28 degradadas; **0 errores** |

**Causa raíz** (log del servidor, 10 de 10):
`TimeoutError: QueuePool limit of size 5 overflow 10 reached, connection timed out, timeout 30.00`.

- El turno de chat tiene una conexión abierta durante toda su transacción, y la recuperación RAG pedía
  una segunda al mismo pool.
- Con 15 turnos ocupando el pool, ninguno conseguía la segunda conexión.
- La primera corrección (solo admisión) no bastó, y eso permitió encontrar la adquisición anidada.

"Degradada" significa HTTP 200 con el texto "En este momento no puedo consultar la base documental…
Si quieres, te comunico con un asesor" y un botón de asesor. Es la misma ruta que con Ollama caído.

## 7. Ollama caído

Servidor `:8011` con `OLLAMA_BASE_URL=http://127.0.0.1:9`. Mismo resultado antes y después de las
correcciones. Hubo 0 errores en el log; solo avisos `ollama /api/embed failed: ConnectError`.

| Petición | Resultado |
|---|---|
| `GET /health` | 200 `ok` |
| `GET /ready` | 503 `{"database":"ok","ollama":"fail","rag_index":"ok"}` |
| Chat de carga especial y de PQRS (RAG) | 200, `AWAITING_USER`, degradado con oferta de asesor (23 ms) |
| Chat de rastreo | 200, "En tránsito…" (8 ms) |
| Chat de cotización | 200, pide las medidas (6 ms) |
| `GET /tracking/TLX-984021-CO` | 200 (1 ms) |
| `POST /cotizar` (ejemplo oficial) | 200: base 65.975 + seguro 125.000 = **190.975** |

## 8. Lo que no se probó

- Prueba sostenida (horas), mezcla real de tráfico y varios procesos o instancias.
- WhatsApp y PBX reales: están en modo `SIMULATED`.
- La PWA: apariencia, usabilidad y accesibilidad.
- *Downgrade* de migraciones: no hubo migraciones nuevas.
- CRIT-004: bloqueado por los datos.
- Capacidad con GPU o con otro valor de `OLLAMA_NUM_PARALLEL`.
