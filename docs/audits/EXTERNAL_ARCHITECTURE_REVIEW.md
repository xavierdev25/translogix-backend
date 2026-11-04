# Revisión arquitectónica externa — TransLogix AI (backend)

Consolidación de los cinco documentos de la auditoría del 2026-11-04:

| Clave | Documento |
|---|---|
| IA | [`INTEGRAL_AUDIT.md`](INTEGRAL_AUDIT.md) |
| TE | [`TEST_EVIDENCE.md`](TEST_EVIDENCE.md) |
| RP | [`REMEDIATION_PLAN.md`](REMEDIATION_PLAN.md) |
| RE | [`RUBRIC_EVALUATION.md`](RUBRIC_EVALUATION.md) |
| PR | [`PRODUCTION_READINESS.md`](PRODUCTION_READINESS.md) |

**Alcance:**
- Solo extrae y compara lo que dicen esos documentos.
- No reaudita, no ejecuta y no modifica código.
- Cuando un tema solicitado no aparece en ellos, se indica **"no cubierto"**.
- Los hechos posteriores a los documentos van aparte, en §11.2.

---

## 1. Resumen general

| Aspecto | Estado (fuente) |
|---|---|
| Estado | Los 4 módulos del caso funcionan: rastreo, cotización, RAG documental y escalamiento humano. Ningún hallazgo CRÍTICO (IA §1, §4) |
| Stack | Python + FastAPI 0.162 (1 proceso, uvicorn); PostgreSQL 18.6 + pgvector 0.8.6; Alembic 0001–0004; Ollama 0.40.14 con `qwen3:8b` (`think:false`, temperatura 0) y `qwen3-embedding:0.6b` (dim. 1024) (IA §2–3, TE §1) |
| Tamaño | `backend/app`: 12.714 líneas; `backend/tests`: 3.707 (IA §2) |
| Contrato | OpenAPI **1.6.0** (CR-008): 21 rutas y 22 operaciones; tipos TS regenerados (IA §2, TE §2) |
| Correcciones | 5 hallazgos ALTOS corregidos (R1 de forma provisional) y TST-01 parcial (IA §4, RP §1) |
| Pruebas | 536 aprobadas (antes 515); cobertura 80 % (antes 73 %); mypy estricto limpio; ruff con 19 E501 heredados (TE §2) |
| Veredicto | **Demo:** lista, con los riesgos de PR §3. **Producción:** NO lista (PR §1) |

## 2. Arquitectura

**Estructura** (IA §3):

```
Canales → FastAPI → middlewares RequestId → CORS → BodySizeLimit
  → ConversationService (P08)
  → DialogueOrchestrator
  → Tracking | Quotation | PQRS | SpecialCargo | EscalationEngine
```

- `RagService` usa su propio pool y llama a `OllamaChat`, con admisión acotada.
- Hay módulos B2B y de personal: `batches`, `shipments`, `tariffs` y `staff`.

| Tema | Lo que dicen los documentos |
|---|---|
| Monolito modular | Un proceso FastAPI con módulos. El término "monolito modular" no se usa (IA §3) |
| DDD / *bounded contexts* | Los módulos B2B y de personal tienen estilo hexagonal (`domain/application/api`). Los flujos comercial y logístico no se mezclan: CR-007 y CR-005 siguen en PROPUESTA y ningún endpoint cruza los dos (IA §3, §6). No hay un análisis DDD explícito |
| Clean Architecture | Puertos y adaptadores: LLM, *embeddings* y TMS se pueden sustituir (IA §3) |
| SOLID / DRY | **No cubierto.** Deuda ARQ-01: conviven dos estilos de módulo (`app/modules/*` y los hexagonales) y `app/main.py` tiene 874 líneas. No se refactorizó (IA §3–4) |
| F.I.R.S.T. | **No evaluado como tal.** Hechos relacionados: la integración usa la base aislada `translogix_test`; los flujos de chat se prueban con Ollama desactivado; la evaluación de IA y la carga están fuera de pytest; se descartó una prueba de concurrencia que no podía fallar (TE §2) |
| Responsabilidades | La lógica sensible es determinista: el LLM no decide tarifas, rastreo, cobertura ni aceptación de carga. `IntentDetector` usa reglas y el LLM solo como respaldo (IA §3) |
| Dependencias | Orquestador → servicios de dominio → `RagService` → `OllamaChat`. La recuperación RAG compartía pool con el turno: era una adquisición anidada (REL-01) |
| Diseño de API | Contrato primero: un sobre de error único con un código estable por fallo, ejemplos validados, tipos TS generados y registro de CR. `X-Request-ID` en las respuestas (IA §3, RP §1) |
| Persistencia | Tablas: `conversations`, `messages`, `handoffs`, `quotes`, `pqrs_tickets`, `audit_events`, `attachments`, `rag_documents`, `rag_chunks`, `shipments*`, `enterprise_clients`, `client_api_keys`, `staff_*` y `tariff_*`. Sin cambios de esquema en la auditoría. Pool SQLAlchemy 5 + 10 (IA §3, §9; TE §1) |
| Concurrencia | Turno atómico con `SELECT … FOR UPDATE` (CON-01); pool RAG propio y admisión de 4 + 8 al LLM (REL-01); *rate limiter* en memoria por proceso (OPS-01) |
| Idempotencia | Lotes B2B por `referencia_lote`; ingesta RAG por *hash* de contenido (IA §5–6). No hay más mecanismos documentados |

Las decisiones se citan como DEC-AUD1 a DEC-AUD5, CR-008 y DEC-SHP7. Se definen en `contracts/DECISIONS.md` y `docs/contract-change-requests.md`, fuera de los cinco documentos.

## 3. Correcciones realizadas

| ID | Problema original | Solución aplicada | Archivos (líneas según RP) | Prueba de verificación | Estado |
|---|---|---|---|---|---|
| REL-01 | 24 preguntas RAG simultáneas: 10 × HTTP 500 (`QueuePool limit … timeout 30.00`), p50 37,5 s | Pool propio para la recuperación RAG + `Admission` (4 en curso, 8 en espera) que degrada por `LLM_NO_DISPONIBLE` | `backend/app/main.py:263`, `backend/app/shared/llm.py:48`, `:111`, `backend/app/shared/config.py` | `tests/test_llm_admission.py` (3); saturación de 24 y 40 → 0 errores | **Implementado y verificado** en el escenario medido. Residual: 4/8 depende del hardware; otro agotamiento del pool → 500 (no medido) |
| SEC-04/05 | 25 MB de JSON → 422 tras leerlo (RSS 76,4 → 181,6 MB); 30 MB de adjunto → 422 tras recibirlo | `BodySizeLimitMiddleware`: 413 `CUERPO_DEMASIADO_GRANDE` antes de leer o durante el *streaming*; 1 MiB por defecto, adjuntos 10 MiB + 64 KiB, lote con su propio 413 | `backend/app/shared/http.py:61`, `:69`; `backend/app/main.py:842`; `backend/app/contracts/common.py`; `backend/app/contracts/openapi.py` | `tests/test_http_body_limit.py` (8); curl: 413 con 0 bytes subidos y RSS 79,0 → 79,1 MB | **Implementado y verificado** |
| CON-01 | Dos turnos simultáneos: uno sobrescribe el dato del otro | `SELECT … FOR UPDATE` en la resolución del turno | `backend/app/db/repositories.py:48`, `:56`; `backend/app/modules/conversation/service.py:137`, `:151` | `tests/conversation/test_conversation_lock.py` (2); sonda CON-01 → `QUOTE_SUMMARY` 45,5 kg y $190.975 | **Implementado y verificado.** Residual: dos primeros mensajes sin sesión pueden crear dos conversaciones (no medido) |
| AI-01 | G04, G05 y G10 escalaban por CRIT-001 (confianza 0,662–0,743) teniendo respuesta en el corpus | Margen sobre el *score* híbrido; regla 4 del *prompt* | `backend/app/modules/rag/service.py:227`, `:121` | Golden set 18/18, 0 alucinaciones (TE §3) | **Implementado y verificado.** Conjunto pequeño (18); juez por palabras clave |
| R1 | "Te responderá en este mismo chat": no existe canal asesor→cliente | Texto "Tu caso ya quedó registrado y está en la cola de asesores de TransLogix." | `backend/app/modules/conversation/orchestrator.py:687`; `docs/api-examples/chat.en_espera_asesor.json`; `docs/FRONTEND_INTEGRATION_EXAMPLES.md` | `test_chat_flows.py::test_waiting_for_an_advisor_does_not_promise_a_reply_in_this_chat` | **Implementado, provisional** (DEC-AUD5). Lo definitivo depende de CR-007; impacto en el front sin verificar |
| TST-01 | Núcleo con poca cobertura | 6 pruebas de flujo de chat contra la base de test, sin Ollama | `tests/conversation/test_chat_flows.py` | Cobertura 73 % → 80 % | **Implementado sin verificación completa.** Siguen bajos: PQRS 36 %, corpus RAG 37 %, servicio RAG 42 %, `ConversationService` 52 % |
| Contrato | — | `ErrorCode.CUERPO_DEMASIADO_GRANDE`, su ejemplo, OpenAPI, tipos TS, CHANGELOG, CR-008, DECISIONS §8; CR-007 → 1.7.0 | `contracts/*` | `python -m app.contracts --check`: al día | **Implementado y verificado** |

**Pendiente sin decisión de negocio** (RP §2):

| ID | Severidad | Archivo | Corrección propuesta |
|---|---|---|---|
| TST-01 (resto) | MEDIO | PQRS y RAG | Más pruebas |
| REL-02 | MEDIO | `backend/app/modules/rag/service.py:383` | Aislar el error por documento |
| OPS-01 | MEDIO | `backend/app/shared/http.py:185` | Limitador compartido en PostgreSQL |
| LLM-01 | BAJO | `backend/app/shared/llm.py` | Un reintento ante `ConnectError` |
| DEMO-01 | BAJO | — | Guía real con `RELECTURA_HUB_FONTIBON`, sin inventar datos |
| INF-04 | INFORMATIVO | — | Reiniciar `:8000` |

**Recomendación futura:** la propuesta de cifrado de PII (RP §4) **no está implementada**.

## 4. Seguridad y ciberseguridad

| Tema | Hallazgo | Severidad | Estado | Consecuencia |
|---|---|---|---|---|
| Autenticación y autorización | Las API keys (`tlx_`) y los tokens de personal (`tlxs_`) se guardan como SHA-256. El token de administración se compara en tiempo constante (IA §3, §7) | — | Positivo | — |
| API keys: *scopes* | **No cubierto** | — | — | — |
| *Multi-tenancy* | **No cubierto** (solo consta la tabla `enterprise_clients`) | — | — | — |
| Aislamiento de sesiones e historial | SEC-01: `GET /chat/sessions/{id}/history` devuelve la conversación sin `X-Client-Id` (la sonda dio 200 con 2 mensajes) | ALTO | Pendiente (Q2) | Basta el `session_id` para leer una conversación |
| Adjuntos: tamaño | SEC-05: se recibían enteros | ALTO | Corregido | — |
| Adjuntos: acceso | SEC-06: `POST /attachments` sin autenticación ni límite (la sonda aceptó 25 de 25) | ALTO | Pendiente (Q3) | Agotamiento del disco: 10 MB por petición |
| PII | PII-01: en claro en `shipment_recipients` (`nombre`, `numero_documento`, `telefono`, `email`, `direccion`) y en el texto de `messages.content_json`; sin retención ni eliminación | ALTO | Pendiente (Q5) | Bloquea producción (Ley 1581 de 2012) |
| PII: lo que ya hay | Usuario externo como HMAC con `PII_HASH_SALT`; el log redacta (`[PII:URL]`); *payload* de *handoff* sin PII (HITL-05) | — | Positivo | — |
| Cifrado | Solo propuesta: Fernet/`MultiFernet` + índice ciego HMAC, `PII_ENCRYPTION_KEYS` y `PII_BLIND_INDEX_KEY`, dependencia nueva `cryptography` y migración 0005 | — | No implementado | — |
| Secretos | `.env` no versionado; ningún secreto en el repositorio ni en los informes; token de las sondas fuera del repo con permisos 600 (IA §4, TE) | — | Positivo | — |
| *Rate limiting* | OPS-01: en memoria y por proceso. Las pruebas de carga lo desactivaron (`CHAT_RATE_LIMIT=0`) | MEDIO | Pendiente | No funciona con varios *workers* |
| Seguridad HTTP | Límite de cuerpo (SEC-04/05); `/docs` y `/openapi.json` públicos (SEC-03). Las cabeceras de seguridad no están cubiertas | ALTO / MEDIO | Corregido / pendiente (Q4) | Superficie innecesaria en producción |
| CORS | El 413 sale dentro de CORS y con `X-Request-ID`, probado en `test_http_body_limit.py`. No hay otra evaluación de CORS | — | Verificado (parcial) | — |
| OWASP API Security | Los documentos no hacen la correspondencia. Orientativa, hecha en esta consolidación: API1 → SEC-01; API4 → SEC-04/05/06 y REL-01; API8 → SEC-03 | — | — | — |
| SQL injection | SEC-07: la ruta de rastreo responde 422 | — | PASS | — |
| Validación de entradas | SEC-02 (enumeración de guías internas): 404 | — | PASS | — |
| *Prompt injection* | RAG-08 (exposición del *prompt*) | — | PASS | — |
| *Webhook* | Firma HMAC de WhatsApp con `compare_digest` | — | Positivo | — |
| Logs y auditoría | Redacción de PII; tabla `audit_events` | — | Positivo | — |
| Dependencias vulnerables | **No cubierto**: no consta ningún análisis de vulnerabilidades de dependencias | — | — | — |

## 5. IA y RAG

| Aspecto | Dato (IA §5, TE §3, §5–7) |
|---|---|
| Modelos | `qwen3:8b` (temperatura 0, sin *thinking*); *embeddings* `qwen3-embedding:0.6b` (1024) |
| *Chunking* | 512 tokens con 64 de solapamiento, metadatos de categoría y zona; `count_tokens` aproximado; 4 documentos y 84 *chunks* |
| Recuperación | Híbrida: pgvector coseno 0,7 + BM25 0,3; filtro por documento según la intención (DOC-OPS, DOC-PQRS) |
| Confianza | `heuristic_v1`, no calibrada (INF-01). Umbral 0,75 de las reglas oficiales (CRIT-001). AI-01 corrigió el cálculo del margen |
| *Grounding* y citas | Controles deterministas: cada número debe estar en la evidencia; sin PII; sin URLs; longitud máxima; `SIN_EVIDENCIA` |
| *Fallbacks* | Ollama caído o saturación: HTTP 200 con oferta de asesor; `/ready` responde 503 |
| Escalamiento | CRIT-001, CRIT-002, CRIT-003 y CRIT-005 verificados. CRIT-004 bloqueado por los datos. CRIT-003 y CRIT-005 en modo contextual, sin aprobar (Q6). Inactividad de 5 min sin usar (HITL-02) |
| Rendimiento | *Embedding*: p50 11–13 ms. 120 tokens: p50 ~1,2 s. Turno RAG: p50 ~1,9 s en serie; 7,4 s con 4 en paralelo. Total: **~0,5 respuestas/s** |
| Precisión | Golden set: 14/18 en bruto (15/18 con el juez revisado) → **18/18**; 13/13 responde; 5/5 se abstiene; **0 alucinaciones** |
| Limitaciones | Conjunto de 18. Juez por palabras clave, con G01 y G04 ampliadas tras revisión manual. Una ejecución post-AI-01 con las palabras clave originales dio 17/18. RAG-02: la recuperación no usa el contexto de la conversación. LLM-01: sin reintentos |

## 6. Pruebas y evidencias

| Tipo de prueba | Resultado anterior | Resultado actual | Evidencia o limitación |
|---|---|---|---|
| Total de pruebas | 515 | 536 | TE §2. Pytest no se desglosa por tipo |
| Cobertura | 73 % | 80 % | Núcleo bajo: ver TST-01 |
| Unit tests | No desglosado | +11 nuevas | `test_http_body_limit.py` (8) y `test_llm_admission.py` (3), sin base de datos |
| Integration tests | No desglosado | +8 nuevas | `test_chat_flows.py` (6) y `test_conversation_lock.py` (2), contra `translogix_test` |
| Contract tests | Al día (1.5.0) | Al día (1.6.0); +2 | El código nuevo y su ejemplo |
| Concurrency tests | CON-01 FAIL; saturación con 10 × 500 | CON-01 PASS; 0 errores con 24 y 40 | La prueba HTTP en proceso se descartó: el cliente ASGI serializa las peticiones |
| Security tests | 2 de 7 sondas SEC en PASS (SEC-02, SEC-07) | 4 de 7 (más SEC-04 y SEC-05) | SEC-01, SEC-03 y SEC-06 en FAIL, pendientes de decisión |
| Sondas (todas) | 17 PASS / 9 FAIL / 1 BLOCKED | 21 / 5 / 1 | RAG-02 y TRK-02 fallan por defectos de la propia sonda; HITL-06 bloqueada |
| RAG Evaluation | 14/18 (15/18 con el juez revisado) | 18/18 | 18 casos; juez por palabras clave |
| mypy | Limpio | Limpio (154 archivos) | Estricto |
| Ruff | 20 | 19 | E501 heredados |
| Alembic | Sin operaciones | Sin operaciones | Sin migraciones; *downgrade* no probado |
| OpenAPI | Al día | Al día (1.6.0) | Tipos regenerados (`b98636cd…afd7`) |

**Resultados no concluyentes:**
- `/cotizaciones` con C = 50 dio 261, 483 y 911 req/s en tres ejecuciones (variación de hasta 3×).
- httpx recibe `ReadError` ante el 413 temprano; se verificó con curl.

**Entorno:** portátil Apple M5 Pro (18 núcleos, 24 GiB); 1 proceso; pruebas de 0,5 a 16 s; sin *soak*.

**No probado:**
- mezcla real de tráfico y varias instancias;
- WhatsApp y PBX reales;
- la PWA;
- *downgrade* de migraciones;
- CRIT-004;
- GPU u otro valor de `OLLAMA_NUM_PARALLEL`.

**No reproducibles desde el repositorio:** las sondas (`probes_*.json`) y el script de saturación
(`<scratchpad>/rag_saturation.py`) están fuera del repo (TE §4, §6). Sí están en el repo
`tests/ai_eval/` y `tests/load/run_load.py`.

## 7. Decisiones pendientes

| ID | Decisión | Recomendación del agente | Impacto | ¿Bloquea? |
|---|---|---|---|---|
| Q1 | Envío B2B con `mercancia_peligrosa=true` sin política documentada (hoy se acepta; DEC-SHP7 PROVISIONAL) | Crearlo con `review_status = PENDIENTE` (motivo `MERCANCIA_PELIGROSA`) hasta que lo apruebe un REVISOR_ESPECIALIZADO, según CR-006 | Spec §8; rúbrica 2.2 | Producción (P5) |
| Q2 | ¿Exigir `X-Client-Id` en el historial? | Sí; publicar 2.0.0 coordinado con el front, o pactar la excepción previa a la Puerta A | MAJOR | Producción (P4) |
| Q3 | Límite de `POST /attachments`: (a) por IP, confiando en `CF-Connecting-IP`; (b) con `session_id` obligatorio | (b), en la misma versión que Q2 | MAJOR | Producción (P3) |
| Q4 | ¿Desactivar `/docs` y `/openapi.json` en producción? | Sí, con una variable de entorno | Superficie expuesta | Producción (P8) |
| Q5 | ¿Aprobar el cifrado del RP §4? ¿Plazos de retención y eliminación? | Aprobar; fijar la retención con el área legal (Ley 1581 de 2012) | Dependencia nueva y migración | Producción (P1, P2) |
| Q6 | CRIT-003 y CRIT-005: ¿modo contextual o literal? | Contextual, con aprobación escrita del negocio | Rúbrica 2.2 | No figura como bloqueo |
| Q7 | ¿Qué cuentas son `CORPORATIVO_GOLD` (CRIT-004)? | Que el negocio entregue la lista | CRIT-004 no demostrable | Riesgo de demo |
| Q8 | ¿Qué hacer tras 5 min de inactividad? | Recordatorio a los 5 min y cierre a los 30, solo en web chat | Rúbrica 2.2 | No figura como bloqueo |
| Q9 | Respuestas del front a CR-007 | Con la pregunta 1 respondida empieza P0a (1.7.0) | R1 definitivo, R2 | Riesgo de demo (el asesor no responde) |
| Q10 | ¿Infraestructura del LLM y pico por hora? | Medir con la mezcla real; GPU o varias instancias de Ollama | Capacidad | Producción (P6) |

## 8. Integración con el frontend

| Tema | Cambio |
|---|---|
| Versión | 1.5.0 → **1.6.0** (CR-008). CR-007 (CRM) pasa a **1.7.0** |
| Endpoints | Los documentos no registran endpoints nuevos ni modificados |
| Código de error nuevo | `CUERPO_DEMASIADO_GRANDE` (HTTP 413) con `details.max_bytes`: 1.048.576 por defecto y 10.551.296 en adjuntos. Se documenta en toda operación con cuerpo, salvo el lote B2B, que mantiene `LOTE_DEMASIADO_GRANDE` |
| *Headers* | Ninguno nuevo. El 413 incluye `X-Request-ID` y las cabeceras CORS |
| Autenticación y autorización | Sin cambios en 1.6.0. Futuros MAJOR propuestos: `X-Client-Id` obligatorio en el historial (Q2) y `session_id` obligatorio en adjuntos (Q3) |
| DTOs | Los documentos no registran cambios en DTOs. Tipos TS regenerados |
| Respuestas | El texto de espera del asesor cambia (R1). Bajo saturación, un turno con IA responde 200 degradado con botón de asesor en lugar de 500 |
| Documentación | `/docs` podría desactivarse en producción (Q4) |
| Pendiente en CR-007 | `interactive_reply_id` (R2) |
| Compatibilidad | Los documentos no la clasifican explícitamente para 1.6.0; solo marcan Q2 y Q3 como MAJOR |

**Antes de desplegar, el front debe:**
1. tratar el 413 `CUERPO_DEMASIADO_GRANDE`;
2. actualizar el texto de espera si lo compara o lo muestra fijo;
3. tratar la respuesta degradada como un caso normal;
4. regenerar o actualizar los tipos;
5. tener en cuenta que, según los documentos, el `:8000` del túnel corre código anterior (ver §11.2).

## 9. Evaluación KODO TAKAI 2026 (RE)

| Indicador | Puntuación | Justificación resumida | Limitación |
|---|---|---|---|
| 1.1 Funcionamiento general | 2 (backend) | 536 pruebas en verde; 4 módulos de punta a punta; sin errores con Ollama caído ni bajo saturación | Sin canal asesor→cliente; R2; PWA sin inspeccionar |
| 1.4 Velocidad de IA | 2 | Rastreo en chat p50 4,7 ms; RAG p50 ~1,9 s; degradación inmediata | ~0,5 respuestas/s; sin *streaming* |
| 1.5 Precisión de IA | 2 | 18/18, 0 alucinaciones, 5/5 abstenciones | 18 casos; juez por palabras clave; confianza no calibrada |
| 2.2 Reglas de negocio | 2 | Fórmula exacta (190.975); TEST_ONLY separado; PQRS; CRIT-001 a CRIT-005 | Q1, Q6, Q7 y Q8 |
| 2.4 Utilidad real | 2 (evidencia técnica) | El 55 % (rastreo) sin humano ni LLM; cotización sin errores aritméticos | CR-007; validación con usuarios N/O |
| 2.5 Potencial de crecimiento | 2 | Contrato versionado; puertos sustituibles; API B2B; tarifas versionadas | Limitador local; un solo Ollama; PII sin cifrar |

**Sin evaluar (N/O):**
- 1.2 Apariencia visual, 1.3 Facilidad de uso, 2.1 Idea de negocio, 2.3 Viabilidad económica;
- 3.1 Claridad al explicar el problema, 3.2 Seguridad al hablar, 3.3 Manejo del tiempo, 3.4 Apoyo
  visual, 3.5 Presentación visual y vestimenta.

Requieren la PWA, un modelo de costos o la exposición. Ninguna puntuación es 3, y RE atribuye la brecha a Q1–Q10 y CR-007.

## 10. Riesgos y bloqueadores

**Equivalencia de esta consolidación:** P0 = CRÍTICO, P1 = ALTO, P2 = MEDIO, P3 = BAJO.
- Los documentos usan la escala de severidad, no P0–P3.
- En PR, "P1–P8" son **identificadores de bloqueo**, no prioridades.

| Prioridad | ID | Problema | Impacto | Estado | Acción |
|---|---|---|---|---|---|
| P0 | — | Ninguno | — | — | — |
| P1 | SEC-06 | Adjuntos sin autenticación ni límite | Disco | Pendiente | Q3 |
| P1 | SEC-01 | Historial sin `X-Client-Id` | Lectura de conversaciones ajenas | Pendiente | Q2 |
| P1 | PII-01 | PII en claro; sin retención | Legal | Pendiente | Q5 |
| P1 | BIZ-01 | Mercancía peligrosa aceptada en lotes | Spec §8 | Pendiente | Q1 / CR-006 |
| P1 | CAP-01 | ~0,5 respuestas IA/s | Capacidad sin demostrar | Pendiente | Q10 |
| P1 | REL-01, SEC-04/05, CON-01, AI-01, R1 | Ver §3 | — | Corregidos (R1 provisional) | — |
| P2 | SEC-03 | `/docs` público | Superficie | Pendiente | Q4 |
| P2 | HITL-02 | Inactividad de 5 min sin usar | Regla oficial | Pendiente | Q8 |
| P2 | HITL-03 | CRIT-004 no demostrable | Demo | Pendiente | Q7 |
| P2 | R2 | Sin `interactive_reply_id` | Los botones llegan como texto | Pendiente | CR-007 |
| P2 | REL-02 | Un documento corrupto aborta la reindexación | Ingesta | Pendiente | RP §2 |
| P2 | OPS-01 | Limitador por proceso | Escalado | Pendiente | RP §2 |
| P2 | TST-01 | Cobertura baja del núcleo | Regresiones | Parcial | RP §2 |
| P3 | LLM-01 | Sin reintentos | Degradación innecesaria | Pendiente | RP §2 |
| P3 | RAG-02 | RAG sin contexto de la conversación | Seguimientos | Pendiente | — |
| P3 | DEMO-01 | Sin guía con `RELECTURA_HUB_FONTIBON` | Demo | Pendiente | RP §2 |
| P3 | ARQ-01 | Dos estilos de módulo; `main.py` grande | Mantenibilidad | No se corrige | — |

**Riesgos para la demostración** (PR §3):
- `:8000` con código anterior;
- modelo en frío;
- degradación con más de 12 consultas simultáneas;
- CRIT-004 no demostrable;
- falta la guía con `RELECTURA_HUB_FONTIBON`;
- el asesor no puede responder;
- preguntas fuera del corpus (medido: se abstiene o escala 5/5).

**Bloqueos para producción** (PR §1, P1–P8):
- P1 PII en claro;
- P2 retención;
- P3 adjuntos;
- P4 historial;
- P5 mercancía peligrosa;
- P6 capacidad;
- P7 instancia única: limitador local; WhatsApp y PBX en `SIMULATED`;
- P8 `/docs`.

## 11. Discrepancias entre documentos

### 11.1 Dentro de los cinco documentos (se registran, no se reconcilian)

| # | Discrepancia | Dónde |
|---|---|---|
| D1 | **Línea base del RAG:** IA dice 14/18 → 18/18. RP (AI-01) y RE (1.5) dicen 15/18 "con el juez revisado". TE da las dos cifras | IA §4–5; RP §1; RE 1.5; TE §3 |
| D2 | **"4 de 13" escaladas** (IA §1) frente a TE §3: 3 escaladas por CRIT-001 (G04, G05, G10) y 1 falso negativo del juez sin escalar (G01) | IA §1; TE §3 |
| D3 | **Cuántos ALTOS se corrigieron:** IA §1 dice "cuatro" y la tabla de IA §4 tiene 5 (con R1). RP §1 lista 6 (añade TST-01 parcial), aunque IA clasifica TST-01 como MEDIO pendiente | IA §1, §4; RP §1 |
| D4 | **TST-01 está en "Corregido" y en "Pendiente"** a la vez. RP omite `ConversationService` 52 %, que IA sí incluye | RP §1–2; IA §4 |
| D5 | **Pruebas nuevas:** IA §9 suma 19 (8 + 3 + 6 + 2); TE dice 21 (+2 de contrato) | IA §9; TE §2 |
| D6 | **"18/18 en dos ejecuciones posteriores"** (RP), sin mencionar la ejecución post-AI-01 con las palabras clave originales (17/18) | RP §1; TE §3 |
| D7 | **Confianza de G04, G05 y G10:** RP dice "entre 0,66 y 0,74"; TE da 0,662, 0,740 y 0,743 | RP §1; TE §3 |
| D8 | **Severidad frente a bloqueo:** SEC-03 y OPS-01 son MEDIO en IA, pero bloqueos de producción en PR (P8 y P7) | IA §4; PR §1 |
| D9 | **Bloqueos de producción:** IA §1 lista 5; PR lista 8 (añade la retención por separado, instancia única y canales simulados, y `/docs`) | IA §1; PR §1 |
| D10 | **Identificadores repetidos con otro significado:** RAG-02 es una sonda (efectivo) en TE y un hallazgo BAJO (sin contexto) en IA; HITL-02/HITL-03 son hallazgos en IA y sondas aprobadas en TE | IA §4; TE §4 |
| D11 | **Numeración de migraciones:** RP §4 propone la 0005 para el cifrado y mover el CRM a 0006; IA y RP citan CR-007 como 1.7.0 sin aclarar su migración | RP §4, Q9 |
| D12 | **Evidencia no reproducible desde el repo:** sondas y saturación (REL-01, CON-01 de punta a punta, SEC-04/05 con curl) | TE §4, §6 |
| D13 | **Referencias externas:** DEC-AUD1–5, CR-008, CR-006 y DEC-SHP7 se citan pero se definen fuera de los cinco documentos | Todos |

### 11.2 Hechos posteriores a los documentos (no constan en ellos)

Estos hechos ocurrieron después de redactar los documentos, en la misma sesión:
- **Commit y push:** los cambios ya están en `main` con el commit `22d1ecc`. IA (cabecera) todavía dice "sin commit".
- **Servidor `:8000`:** se reinició con 1.6.0. Se comprobó en local y por el túnel: `/ready` OK, versión 1.6.0, rastreo 200, 413 con un cuerpo de 1,2 MB y una respuesta RAG citada.
- **Documentos desactualizados:** IA §2, INF-04; RP §2; RE 1.1; y PR §3–4 todavía lo dan como pendiente.

## 12. Conclusión

| Pregunta | Respuesta (según los documentos) |
|---|---|
| Implementado correctamente | Fórmula oficial de cotización; separación TEST_ONLY; rastreo determinista; RAG híbrido con controles deterministas; CRIT-001, CRIT-002, CRIT-003 y CRIT-005; degradación sin Ollama; límite de cuerpo; bloqueo por turno; pool RAG propio y admisión al LLM; contrato 1.6.0 |
| Demostrado con pruebas | 536 pruebas; 18/18 y 0 alucinaciones (con las limitaciones de §5); 0 errores con 24 y 40 RAG simultáneas; 413 sin leer el cuerpo; CON-01 contra el servidor real; Ollama caído sin errores |
| No demostrado | 18.000 interacciones/día; comportamiento sostenido; varias instancias; canales reales; CRIT-004; PWA; precisión con preguntas reales; *downgrade* de migraciones |
| Antes de la demo | Reiniciar `:8000` (ya hecho, §11.2); ensayar los 4 flujos con una pregunta de calentamiento; avisar al front del texto y del 413 |
| Bloquea producción | P1–P8 de PR: PII, retención, adjuntos, historial, mercancía peligrosa, capacidad, instancia única y canales simulados, `/docs` |
| Requiere decisión del equipo | Q1–Q10; prioridad recomendada en PR §4: Q1, luego Q6–Q8, luego Q2 + Q3 (MAJOR), Q5, CR-007, Q10, TST-01/REL-02 |
