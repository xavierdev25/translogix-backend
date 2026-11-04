# Auditoría integral — TransLogix AI (backend)

- **Fecha:** 2026-11-04
- **Alcance:** repositorio `translogix-ai`: backend, contrato, datos y documentación. Se contrasta con:
  - la especificación oficial del reto KODO TAKAI (`data/source/asd.md`);
  - las fuentes de datos (`data/source/*`);
  - las decisiones aprobadas (`contracts/DECISIONS.md`);
  - la rúbrica.
- **No cubre (no es observable desde el backend):**
  - la PWA: apariencia, navegación y facilidad de uso;
  - la exposición del equipo.
- **Base auditada:** commit `6cc4f4c` (contrato 1.5.0). **Resultado:** contrato **1.6.0** (CR-008), sin
  commit. Los cambios quedan en el árbol de trabajo para revisión.

Documentos de la auditoría:

| Documento | Contenido |
|---|---|
| Este | Estado real, arquitectura y hallazgos |
| [`TEST_EVIDENCE.md`](TEST_EVIDENCE.md) | Pruebas, mediciones y comparación antes/después |
| [`REMEDIATION_PLAN.md`](REMEDIATION_PLAN.md) | Cada hallazgo con corrección, prueba y estado, y las decisiones pendientes |
| [`RUBRIC_EVALUATION.md`](RUBRIC_EVALUATION.md) | Matriz 0–3 |
| [`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md) | Bloqueos para producción y riesgos de la demostración |

---

## 1. Resumen ejecutivo

**El backend cumple el caso de negocio en sus cuatro módulos:**

| Módulo | Resultado |
|---|---|
| Rastreo | Traduce los estados técnicos con el mapa oficial |
| Cotización | Fórmula oficial exacta, verificada con el ejemplo del reto: total 190.975 |
| RAG documental | 18/18 aciertos en el conjunto de evaluación y **0 alucinaciones** |
| Escalamiento humano | CRIT-001 a CRIT-005 |

Las rutas deterministas no dependen del LLM, y si Ollama cae el sistema se degrada sin errores.

**Se encontraron y corrigieron cuatro problemas ALTOS medibles:**

1. **Bajo carga, el chat con IA respondía 500.** REL-01: con 24 preguntas documentales simultáneas, 10
   fallaban porque el pool de base de datos se bloqueaba a sí mismo.
2. **Un cuerpo enorme se almacenaba entero antes de validarlo.** SEC-04/05: 25 MB de JSON subían la
   memoria del proceso de 76 a 182 MB.
3. **Dos mensajes simultáneos de la misma conversación perdían un dato.** CON-01.
4. **El RAG escalaba preguntas que sí tenían respuesta en el corpus** (4 de 13). La causa eran dos
   errores de precisión: el margen se calculaba sobre la similitud coseno, pero el ranking es híbrido;
   y una regla del *prompt* hacía abstenerse ante ítems de una lista.

Además se corrigió el texto que prometía al cliente "Te responderá en este mismo chat" (R1), una
respuesta que el backend no puede dar.

**No está listo para producción.** Bloqueos:
- PII sin cifrar (`shipment_recipients`, `messages`) y sin política de retención ni eliminación;
- subida de adjuntos sin autenticación ni límite de tasa;
- historial de chat accesible solo con el `session_id`;
- mercancía peligrosa aceptada en lotes B2B sin una política documentada;
- capacidad del LLM medida en **~0,5 respuestas por segundo** en el equipo de la auditoría.

Este último punto **no demuestra** las 18.000 interacciones diarias del caso: no se hizo una prueba
sostenida. Ver [`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md).

## 2. Estado real del proyecto

| Aspecto | Estado (verificado) |
|---|---|
| Código | `backend/app` tiene 12.714 líneas Python y `backend/tests`, 3.707 |
| Contrato | OpenAPI 1.6.0: 21 rutas y 22 operaciones. Modelos Pydantic, ejemplos, *mocks* y tipos TS generados. `python -m app.contracts --check` dice que está al día |
| Base de datos | PostgreSQL 18.6 + pgvector 0.8.6. Migraciones 0001–0004; dev y test en `0004`; `alembic check` sin operaciones pendientes |
| IA local | Ollama 0.40.14: `qwen3:8b` (`think:false`, temperatura 0) y `qwen3-embedding:0.6b` (dimensión 1024). Corpus: 4 documentos, 84 *chunks* |
| Pruebas | 536 aprobadas (515 antes). Cobertura 80 % (73 % antes). ruff: 19 avisos E501 heredados (20 antes). mypy estricto limpio en `app` y `tests` |
| Datos de demo | 5 guías TMS simuladas, la matriz tarifaria CSV y el directorio de cobertura. Las cuentas simuladas no tienen ningún NIT `CORPORATIVO_GOLD` |
| Canales | Web chat (real), WhatsApp y PBX en modo `SIMULATED` |
| Servidor de desarrollo (`:8000`, túnel) | Sigue con el código **anterior** a esta auditoría. Hay que reiniciarlo para publicar 1.6.0 |

## 3. Arquitectura actual

```
Canales: web chat · WhatsApp (simulado) · PBX (simulado) · API B2B · personal interno
        │  FastAPI 0.162, 1 proceso
        │  middlewares: RequestId → CORS → BodySizeLimit (nuevo, CR-008)
        ▼
ConversationService (P08)
    turno atómico; bloqueo de fila de la conversación (nuevo)
        │
        ▼
DialogueOrchestrator ── IntentDetector (reglas; LLM solo como respaldo)
        │
        ├─ Tracking ─────── DatasetTmsAdapter + StateMap ...... determinista
        ├─ Quotation ────── provisional-v1 (fórmula oficial) .. determinista
        ├─ PQRS ─────────── plazos y documentos + RAG (DOC-PQRS)
        ├─ SpecialCargo ─── RAG (DOC-OPS)
        └─ EscalationEngine  CRIT-001…005 (reglas_escalacion_humana.json) → Handoff

RagService
    pgvector coseno 0,7 + BM25 0,3; confianza heuristic_v1
    pool propio (nuevo)
    controles deterministas sobre la respuesta
        │
        ▼
OllamaChat
    admisión acotada: 4 en curso + 8 en espera (nuevo)

Módulos B2B y de personal (estilo hexagonal: domain/application/api):
    batches · shipments · tariffs (excedente-v1, TEST_ONLY) · staff (tlxs_)

PostgreSQL:
    conversations, messages, handoffs, quotes, pqrs_tickets, audit_events, attachments,
    rag_documents, rag_chunks, shipments*, enterprise_clients, client_api_keys,
    staff_*, tariff_*
```

**Lo que funciona bien:**
- **Contrato primero.** Un único sobre de error y un código estable por fallo. Ejemplos validados, tipos
  TS generados y registro de solicitudes de cambio (CR).
- **Puertos y adaptadores.** El LLM, los *embeddings* y el TMS se pueden sustituir.
- **Lógica sensible determinista.** El LLM no decide tarifas, estados de rastreo, cobertura ni la
  aceptación de carga. Cumple la regla "no usar el LLM como fuente de verdad".
- **PII.** El usuario externo se guarda como HMAC con `PII_HASH_SALT`. El log redacta URLs y PII
  (`[PII:URL]`). Las API keys y los tokens se guardan como SHA-256.

**Deuda (no se corrigió por ser estética):**
- conviven dos estilos de módulo: `app/modules/*` y `app/{batches,shipments,tariffs,staff}` hexagonales;
- `app/main.py` tiene 874 líneas;
- 19 líneas de más de 100 caracteres en textos de usuario (`orchestrator.py`, `intents.py`).

## 4. Hallazgos por severidad

La evidencia, el archivo y las líneas, la corrección y la prueba de cada uno están en
[`REMEDIATION_PLAN.md`](REMEDIATION_PLAN.md).

### CRÍTICO

Ninguno. No se encontró pérdida de datos persistente, ejecución remota ni exposición de secretos.
`.env` no está versionado y ningún secreto aparece en el repositorio ni en los informes.

### ALTO

| Id | Hallazgo | Estado |
|---|---|---|
| REL-01 | 500 bajo ráfagas de preguntas con IA: agotamiento del pool por adquisición anidada (turno + recuperación RAG) | **Corregido** (DEC-AUD2/3) |
| SEC-04/05 | Sin límite de tamaño de cuerpo; los adjuntos se reciben enteros antes de validar el tamaño | **Corregido** (CR-008, DEC-AUD1) |
| CON-01 | Actualización perdida con dos turnos simultáneos de una conversación | **Corregido** (DEC-AUD4) |
| AI-01 | Precisión del RAG: margen sobre coseno y no sobre el *score* híbrido; regla 4 del *prompt* | **Corregido** (golden set 14/18 → 18/18) |
| R1 | El chat promete una respuesta del asesor por un canal que no existe | **Corregido provisionalmente** (DEC-AUD5); lo definitivo llega con CR-007 |
| SEC-06 | `POST /attachments` sin autenticación ni límite de tasa (hasta 10 MB por archivo en disco) | Pendiente: decisión Q3 |
| SEC-01 | `GET /chat/sessions/{id}/history` devuelve la conversación sin `X-Client-Id` | Pendiente: decisión Q2 (endurece el contrato) |
| PII-01 | PII en claro en `shipment_recipients` y en el texto de `messages.content_json`; sin retención ni eliminación | Pendiente: propuesta de cifrado (Q5) |
| BIZ-01 | Mercancía peligrosa aceptada en lotes B2B sin política documentada (DEC-SHP7, PROVISIONAL) | Pendiente: decisión Q1 |
| CAP-01 | Capacidad del LLM: ~0,5 respuestas/s en un único Ollama. Se desconoce el reparto de las 18.000 interacciones diarias | Pendiente: infraestructura (Q10) |

### MEDIO

| Id | Hallazgo |
|---|---|
| SEC-03 | `/docs` y `/openapi.json` públicos (decisión Q4) |
| HITL-02 | `tiempo_maximo_inactividad_minutos` (5) de las reglas no se usa. `idle_reminder_minutes_web_chat` existe en la configuración, pero nada lo lee. La acción esperada no está definida (Q8) |
| HITL-03 | CRIT-004 no se puede demostrar: ningún NIT es `CORPORATIVO_GOLD` y ninguna cuenta supera 500 envíos/mes. No se inventan datos (Q7) |
| R2 | El web chat no tiene `interactive_reply_id`; los botones "novedad" y "otra_guia" solo llegan como texto (incluido en CR-007) |
| REL-02 | Un documento corrupto aborta toda la reindexación |
| OPS-01 | El *rate limiter* es en memoria y por proceso: no sirve con varios *workers* |
| TST-01 | Poca cobertura del núcleo: PQRS 36 %, corpus RAG 37 %, servicio RAG 42 % (sin Ollama), `ConversationService` 52 % |

### BAJO

| Id | Hallazgo |
|---|---|
| LLM-01 | Sin reintentos ante un fallo transitorio de Ollama: el turno se degrada a la primera |
| RAG-02 | La recuperación RAG no usa el contexto de la conversación. La prueba de seguimiento RAG-07 pasó, pero solo por el contenido de la pregunta |
| DEMO-01 | `RELECTURA_HUB_FONTIBON`, el estado del ejemplo oficial, tiene la traducción exacta del enunciado en `tms_state_map.json`, pero ninguna de las 5 guías del *fixture* pasa por él |
| ARQ-01 | Dos estilos de módulo y un `main.py` grande (no se refactoriza) |

### INFORMATIVO

| Id | Hallazgo |
|---|---|
| INF-01 | `confidence_score` (`heuristic_v1`) es una heurística, no una probabilidad calibrada. El umbral 0,75 viene de las reglas oficiales |
| INF-02 | CRIT-003 y CRIT-005 usan los modos contextuales (`claim_context`, `incident_context`) en lugar de la lectura literal del JSON. Es una interpretación documentada; **no se reconcilió** sin aprobación (Q6) |
| INF-03 | La escalada de PETICION (R4) es correcta según la especificación oficial |
| INF-04 | El servidor `:8000` del túnel ejecuta el código anterior a esta auditoría |

## 5. Revisión de IA y RAG (fase 3)

**Ingesta:**
- *chunks* de 512 tokens con 64 de solapamiento y metadatos de categoría y zona (spec §4);
- idempotente por *hash* de contenido;
- `count_tokens` es una aproximación;
- la lista de documentos es fija (4).

**Recuperación:**
- híbrida: pgvector coseno 0,7 + BM25 0,3;
- filtro por documento según la intención (DOC-OPS para carga especial, DOC-PQRS para PQRS).

**Generación:**
- `qwen3:8b`, temperatura 0, sin *thinking*;
- *prompt* que prohíbe inventar;
- controles deterministas sobre la respuesta: cada número debe estar en la evidencia, sin PII, sin
  URLs, longitud máxima y `SIN_EVIDENCIA`.

**Defecto corregido (AI-01):**
- El margen de la confianza se calculaba sobre la similitud coseno, aunque el ranking que ve el usuario
  es híbrido. Dos *chunks* que coinciden (por ejemplo, la lista de prohibidos y la guía de servicio)
  parecían una ambigüedad. Por eso CRIT-001 escalaba G04 (efectivo), G05 (pólvora) y G10 (pintura con
  solventes).
- La regla 4 del *prompt* hacía responder `SIN_EVIDENCIA` cuando el ítem estaba dentro de una lista.

| | Línea base (14/18) | Después (18/18) |
|---|---|---|
| Responde cuando debe | 9/13 | 13/13 |
| Se abstiene cuando debe | 5/5 | 5/5 |
| Alucinaciones | 0 | 0 |

Ver la nota de transparencia sobre las palabras clave en [`TEST_EVIDENCE.md`](TEST_EVIDENCE.md) §3.

**Human-in-the-loop frente a `reglas_escalacion_humana.json`:**

| Criterio | Regla | Estado |
|---|---|---|
| CRIT-001 | `confidence < 0,75` | Verificado |
| CRIT-002 | Lista de palabras clave legales y de alto riesgo | Verificado: "demandar ante la Supertransporte" y "ladrones" |
| CRIT-003 | > 5.000.000 COP | Verificado con PQRS y valor declarado alto |
| CRIT-004 | Cuenta Key Account | **Bloqueado** por los datos |
| CRIT-005 | Mercancía peligrosa e incidente | Verificado con TLX-310482-CO |

- El *payload* de transferencia tiene los campos de `flujo_transferencia_pwa` y no lleva PII.
- `maximo_intentos_fallidos_entregados` (2) sí se carga en el motor.
- La inactividad de 5 minutos no se usa (HITL-02).

## 6. Reglas de negocio (fase 4)

**Cotización oficial (`provisional-v1`, `/cotizar` y chat):**
- Peso volumétrico = A × A × L / 4000; peso facturable = máx(real, volumétrico).
- Seguro = máx(1 % del valor declarado, 2.500); total = base + seguro.
- Ejemplo oficial: 40×50×60 cm, 45,5 kg y 12.500.000 COP. Volumétrico 30 kg, facturable 45,5 kg, base
  65.975, seguro 125.000, **total 190.975**. Verificado por HTTP y en el chat (prueba nueva).
- Seguro mínimo: 1 % de 100.000 → 2.500.
- La alternativa del CSV (3.500) queda como supuesto declarado, no se aplica.

**Matriz TEST_ONLY separada:**
- `/cotizaciones` (`excedente-v1`) solo usa perfiles `TEST_ONLY` mientras
  `TARIFF_ENVIRONMENT=TEST_ONLY`;
- la respuesta se marca `comercial: false`;
- el chat no la usa.

**Envíos masivos:**
- idempotencia por `referencia_lote`;
- 413 `LOTE_DEMASIADO_GRANDE` propio;
- validación por ítem.
- **BIZ-01:** la mercancía peligrosa se acepta sin validar la clase (DEC-SHP7 PROVISIONAL). La
  especificación (§8) y la regla de esta auditoría piden no aceptar automáticamente sin política (Q1).

**Flujos comercial y logístico:** no se mezclan. El lead y la gestión comercial (CR-007) y los estados
logísticos (CR-005) siguen en PROPUESTA. Ningún endpoint cruza los dos flujos.

**PQRS:** plazos del protocolo (art. 1028 C. Co.: 3 días para la avería oculta; 10 días hábiles),
documentos obligatorios y escalada de PETICION (R4) correctos. Cobertura de pruebas baja (TST-01).

**Carga especial:** las respuestas salen solo del manual de operaciones (MSDS, IMO, clases ONU y lista
de prohibidos). Ninguna aceptación es automática en el chat.

## 7. Seguridad (fase 5)

**Pruebas que pasan:**
- SQL injection en la ruta de rastreo (SEC-07);
- enumeración de guías internas (SEC-02);
- exposición del *prompt* (RAG-08);
- firma HMAC del *webhook* de WhatsApp con `compare_digest`;
- token de administración en tiempo constante;
- *hash* de identificadores externos.

**Corregido:** límite de tamaño de cuerpo (SEC-04/05).

**Pendiente con decisión:** SEC-01, SEC-03 y SEC-06.

**Cifrado de PII (PII-01):** solo se presenta la propuesta, que requiere aprobación (dependencia nueva
`cryptography` y gestión de claves). Ver [`REMEDIATION_PLAN.md`](REMEDIATION_PLAN.md) §4. No se
inventaron claves ni se guardó ningún secreto.

## 8. Rendimiento y fiabilidad (fase 6)

Resumen; el detalle, el hardware y la metodología están en [`TEST_EVIDENCE.md`](TEST_EVIDENCE.md) §5.

**Rutas deterministas (1 proceso, concurrencia 50, sin errores):**

| Ruta | Rendimiento | p95 |
|---|---|---|
| Tracking | ~1.100 req/s | ~125 ms |
| Turno de chat de rastreo | ~400 turnos/s | ~355 ms |

**IA:**

| Medición | Resultado |
|---|---|
| *Embedding* | p50 11–13 ms |
| Generación de 120 tokens | p50 ~1,2 s |
| Turno RAG completo | p50 ~1,9 s en serie; 7,4 s con 4 en paralelo |
| Rendimiento del LLM | ~0,5 respuestas/s, limitado por Ollama |

**Saturación (24 preguntas RAG simultáneas):**

| | Errores | p50 | Respuestas |
|---|---|---|---|
| Antes | **10 × 500** | 37,5 s | |
| Después | **0** | 1,6 s | 12 con citas; 12 degradadas al instante con oferta de asesor |

Con 40 simultáneas: 0 errores.

**Ollama caído:**
- `/ready` responde 503 (`ollama: fail`);
- rastreo, cotización y `/cotizar` siguen funcionando;
- RAG y PQRS se degradan con oferta de asesor;
- 0 errores.

**Memoria (RSS del proceso):**
- con el servidor recién arrancado, la batería de carga lo llevó de 24,6 a 98,5 MB;
- después de las correcciones, con el servidor ya cargado por las sondas, la evaluación y la saturación,
  pasó de 111,5 a 114,1 MB;
- la subida de 25 MB ya no cambia la memoria.

## 9. Correcciones aplicadas en esta auditoría

| Archivo | Cambio |
|---|---|
| `backend/app/modules/rag/service.py` | Margen sobre el *score* híbrido; regla 4 del *prompt* (AI-01) |
| `backend/app/shared/http.py`, `backend/app/main.py`, `backend/app/shared/config.py` | `BodySizeLimitMiddleware` y límites por prefijo (SEC-04/05) |
| `backend/app/shared/llm.py`, `backend/app/main.py` | `Admission` en `OllamaChat`; pool propio para la recuperación RAG (REL-01) |
| `backend/app/db/repositories.py`, `backend/app/modules/conversation/service.py` | `SELECT … FOR UPDATE` en la resolución del turno (CON-01) |
| `backend/app/modules/conversation/orchestrator.py` | Texto de espera del asesor (R1) |
| Contrato 1.6.0 | `ErrorCode.CUERPO_DEMASIADO_GRANDE`, ejemplo, OpenAPI y tipos TS regenerados; CHANGELOG, CR-008, DECISIONS §8; CR-007 re-asignada a 1.7.0 |
| Pruebas nuevas | `tests/test_http_body_limit.py` (8), `tests/test_llm_admission.py` (3), `tests/conversation/test_chat_flows.py` (6), `tests/conversation/test_conversation_lock.py` (2), `tests/ai_eval/` (golden set y evaluador), `tests/load/run_load.py` |

No hubo cambios de base de datos: no hace falta migración.
