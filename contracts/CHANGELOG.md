# Changelog del contrato

Versionado semántico sobre `info.version` de `openapi.yaml` (`CONTRACT_VERSION`), aplicable **a
partir de la Puerta A** (contrato congelado):

* **MAJOR**: quitar o renombrar un campo, endpoint o valor de enumerado; volver obligatorio un campo
  de entrada; endurecer una validación.
* **MINOR**: añadir un campo opcional de entrada, un campo de salida, un endpoint o un valor de enumerado.
* **PATCH**: descripciones, ejemplos y documentación.

Antes de la Puerta A las versiones pueden incluir ajustes no aditivos (nadie consume todavía).
Los demás agentes proponen cambios en [`docs/contract-change-requests.md`](../docs/contract-change-requests.md).

## 2.0.0 — 2026-11-04 · remediación prioritaria de seguridad, negocio y capacidad (CR-010)

**MAJOR:** dos operaciones endurecen su validación. Guía para el front en
[`docs/FRONTEND_CAMBIOS_2_0_0.md`](../docs/FRONTEND_CAMBIOS_2_0_0.md) y decisiones en
[`DECISIONS.md`](DECISIONS.md) §10.

| Cambio | Tipo | Motivo |
|---|---|---|
| `GET /chat/sessions/{id}/history` exige el dueño (`X-Client-Id`) o un token de ADMIN o REVIEWER. Sin credencial: 401; sesión ajena: 404; API key: 403 | **Incompatible** | SEC-01 |
| `POST /attachments`: `session_id` obligatorio (de una conversación de web chat del mismo usuario) y `X-Client-Id` obligatorio. Límite por usuario: 429. PDF incompleto: 422 | **Incompatible** | SEC-06 |
| `POST /chat/messages`: solo puede referenciar adjuntos del mismo usuario (otro → 404) | Comportamiento | SEC-06 (IDOR) |
| Esquema de seguridad `ClientId` (cabecera `X-Client-Id`) | Aditivo | SEC-01 y SEC-06 |
| `ShipmentBatchItemResult.estado_revision` (`NO_REQUERIDA`, `PENDIENTE`, `APROBADA`, `RECHAZADA`) y `ReviewStatus`. La mercancía peligrosa nace `PENDIENTE` y solo la decisión de un REVIEWER la cambia | Aditivo | BIZ-01 / Q1 |
| `ErrorCode.SERVICIO_SATURADO` (503 + `Retry-After`) cuando el pool de BD se agota; documentado en toda operación salvo `/health` | Aditivo | CAP-01 |
| `EnvVar.PII_ENCRYPTION_KEYS` (secreto). Sin ella la app no arranca | Aditivo | PII-01 |
| Sin cambio de forma: PII cifrada en reposo, *rate limit* compartido en PostgreSQL y un reintento de Ollama ante `ConnectError` | Interno | PII-01, OPS-01 y LLM-01 |

## 1.7.0 — 2026-11-04 · RBAC mínimo: ADMIN, CLIENT, REVIEWER (CR-009)

Cambio **aditivo** (MINOR) en la forma: se añaden 6 operaciones y alternativas de credencial en 6
operaciones existentes. **Dos cambios de comportamiento:**
- sin credencial, `/handoffs*` y `/admin/rag/reindex` responden **401** (antes 403);
- un token de personal en `POST /shipment-batches` responde **403** (antes 401).

Detalle en [`docs/RBAC.md`](../docs/RBAC.md) y decisiones en [`DECISIONS.md`](DECISIONS.md) §9.

| Cambio | Motivo |
|---|---|
| `Role` (`ADMIN`, `CLIENT`, `REVIEWER`) reemplaza a los roles de personal 1.4.0 (migración 0005: todos pasan a REVIEWER) | RBAC con tres roles exactos |
| `GET /auth/me`, `POST /internal/staff-users`, `PATCH /internal/staff-users/{id}/roles`, `GET /internal/audit-events`, `GET /internal/dangerous-goods` y `POST /internal/shipments/{id}/dangerous-goods-review` | Matriz de autorización |
| `/handoffs*` y reindex aceptan `StaffToken` además de `AdminToken`; los GET de lotes aceptan `StaffToken`/`AdminToken` (ADMIN, lectura global) | Roles ADMIN y REVIEWER; compatibilidad con `X-Admin-Token` |
| 8 ejemplos nuevos (`auth.me`, `staff-users.*`, `audit-events`, `dangerous-goods.*`) | *Mocks* y validación |

## 1.6.0 — 2026-11-04 · endurecimiento tras la auditoría integral (CR-008)

Cambio **aditivo** (MINOR). Implementa [CR-008](../docs/contract-change-requests.md). Ningún campo ni
endpoint cambia. Decisiones en [`DECISIONS.md`](DECISIONS.md) §8 (DEC-AUD1 a DEC-AUD5). Evidencia en
[`docs/audits/TEST_EVIDENCE.md`](../docs/audits/TEST_EVIDENCE.md).

| Cambio | Motivo |
|--------|--------|
| `ErrorCode.CUERPO_DEMASIADO_GRANDE` (413), con `details.max_bytes`. Lo documentan todas las operaciones con cuerpo, salvo el lote B2B, que conserva `LOTE_DEMASIADO_GRANDE`. | SEC-04/05: el cuerpo se almacenaba entero antes de validarlo. |
| Ejemplo `error.cuerpo-demasiado-grande`. | Un ejemplo por código. |
| Comportamiento (sin cambio de forma): bajo saturación, un turno que necesita el LLM responde 200 con la ruta degradada (oferta de asesor) en lugar de 500. | REL-01. |
| Comportamiento: el texto de espera del asesor ya no promete una respuesta en el chat. | R1 (provisional hasta CR-007). |

## 1.5.0 — 2026-11-04 · cotización por perfiles tarifarios (CR-003)

Cambio **aditivo** (MINOR). Implementa [CR-003](../docs/contract-change-requests.md) con las decisiones
de negocio D2–D5. `POST /cotizar` (`provisional-v1`) y el chat no cambian. Decisiones en
[`DECISIONS.md`](DECISIONS.md) §7 (DEC-TAR1 a DEC-TAR7).

| Cambio | Motivo |
|--------|--------|
| `POST /api/v1/cotizaciones` (P1, `createProfileQuotation`): fórmula `excedente-v1` con peso facturable e IVA del perfil; persiste un snapshot inmutable. | Spec 1.1 §6; CR-003. |
| `tariffs.py`: `CotizacionPerfilRequest` (peso y medidas con 2 decimales), `CotizacionPerfilResponse` (valida su propia aritmética), `TarifaPerfilAplicada`, `CiudadRef` y `TipoPerfil` (`TEST_ONLY`/`COMMERCIAL`). | Spec 1.1 §6.3 y §6.5. |
| `EnvVar.TARIFF_ENVIRONMENT` (`TEST_ONLY` por defecto). | Un perfil TEST_ONLY nunca confirma un precio comercial (spec 1.1 §5.1). |
| 2 ejemplos (`cotizaciones.*`): Bogotá → Barranquilla, 6,2 kg: flete 31.500 + IVA 5.985 = 37.485. | Mocks y validación del YAML. |

## 1.4.0 — 2026-11-04 · identidades internas (CR-002)

Cambio **aditivo** (MINOR). Implementa [CR-002](../docs/contract-change-requests.md). Ninguna operación
HTTP existente cambia y no se añade ningún endpoint: las rutas internas que usarán este esquema llegan
con CR-004 y CR-005. Decisiones en [`DECISIONS.md`](DECISIONS.md) §6 (DEC-STF1 a DEC-STF5).

| Cambio | Motivo |
|--------|--------|
| `staff.py`: `StaffRole` (`ASESOR`, `SUPERVISOR`, `OPERADOR_LOGISTICO` y `REVISOR_ESPECIALIZADO`). | CR-002: el asesor es una identidad interna autenticada (spec 1.1 §5.3). |
| `api_spec.py`: esquema de seguridad `StaffToken` (`Authorization: Bearer tlxs_…`) y `StaffTokenDep`, separados de `ApiKey`. Aún no lo usa ninguna ruta publicada, así que el OpenAPI solo cambia de versión. | CR-002. |

## 1.3.0 — 2026-11-04 · envíos masivos B2B (P0)

Cambio **aditivo** (MINOR): nada existente cambia de forma ni de comportamiento.
Decisiones en [`DECISIONS.md`](DECISIONS.md) §5 (DEC-SHP1 a DEC-SHP11).

| Cambio | Motivo |
|--------|--------|
| `POST /api/v1/shipment-batches`, `GET /api/v1/shipment-batches/{batch_id}` y `GET /api/v1/shipment-batches/{batch_id}/items` (P0), con esquema de seguridad `ApiKey` (`Authorization: Bearer`). | Especificación P0 de envíos masivos §5. |
| `shipment_batches.py`: `ShipmentBatchRequest`, `EnvioLoteItem` (hereda los campos canónicos de `CotizacionRequest`), `Destinatario`, `ItemError`, `ShipmentBatchItemResult`, `ShipmentBatchSummary`, `ShipmentBatchResponse`, `ShipmentBatchItemsPage`, `ApiScope`, `BatchStatus` y `BatchItemStatus`. | Spec §5; sin duplicar modelos existentes. |
| `ErrorCode`: `NO_AUTENTICADO` (401), `IDEMPOTENCIA_CONFLICTO` (409), `LOTE_DEMASIADO_GRANDE` (413), `REFERENCIA_DUPLICADA`, `CIUDAD_NO_CUBIERTA`, `TARIFA_NO_DISPONIBLE` y `ARCHIVO_INVALIDO` (reservado). Cada uno con su ejemplo `error.*.json`. | Spec §9. |
| `EnvVar`: `BATCH_MAX_ITEMS`, `BATCH_MAX_BODY_BYTES`, `BATCH_RATE_LIMIT_PER_MINUTE` y `BATCH_LEASE_SECONDS`. | Límites de lote y reanudación (spec §6.5 y §8). |
| 4 ejemplos nuevos (`shipment-batch.*`): petición con un envío válido, una ruta sin tarifa y una referencia repetida; respuesta 201; resumen; página de ítems. | Mocks y validación del YAML. |

## 1.2.0 — 2026-11-03 · candidata a Puerta A (`contracts-v1`)

Alineación con el prompt oficial de P01 y con lo que exigen P02–P09 del Prompt Pack.
Detalle y motivos en [`DECISIONS.md`](DECISIONS.md); incoherencias halladas en `docs/evidence/P01.md`.

| Cambio | Motivo |
|--------|--------|
| `ports.py` declara los 17 puertos (`TmsPort`, `QuotationPort`, `RagPort`, `SpecialCargoPort`, `EscalationPort`, `HandoffPort`, `PqrsPort`, `AccountDirectoryPort`, `LlmPort`, `EmbeddingPort`, `VectorStorePort`, `AuditPort`, `Clock`, además de los 4 de canal y voz) y sus modelos (`AccountProfile`, `LlmMessage/Completion`, `IndexedChunk`, `ScoredChunk`, `AuditEvent`). | Prompt P01 ítem 3. |
| `fakes.py`: un fake determinista por puerto; `FakeConversationService()` sin argumentos responde a los tres canales (con respuesta simulada de respaldo). Sustituye a `GoldenConversationService`. | Prompt P01 ítem 4 y criterio de aceptación. |
| `EstadoTicket.FUERA_DE_PLAZO` y `Elegibilidad.fundamento` (documento y sección). | Prompt P01 (a); P09, DEC-P2. |
| `POST /api/v1/attachments` (P1) y `PATCH /api/v1/handoffs/{id}` (P1). | Prompt P01 (b); P07 (bandeja). |
| `WhatsAppWebhookPayload`: cuerpo tipado del webhook, el mismo para la simulación. | Prompt P01 (c). |
| `Cop` estricto (rechaza `125000.0`, `"125000"`). | Prompt P01 ítem 6. |
| `TmsShipment.intentos_entrega_fallidos` (opcional). | DEC-E4 / P07 (INFO-INTENTOS). |
| `EnvVar.CLOCK_OVERRIDE`; se retira `now_bogota()` (la hora sale del puerto `Clock`). | Bloque común, regla 10. |
| Mensajes por defecto de `RUTA_SIN_TARIFA` y `CIUDAD_NO_RECONOCIDA` alineados con P03 ("no tengo tarifa en la matriz disponible"). | Prompt P03. |
| 35 ejemplos nuevos (89 en total): uno por código de error, `ConversationReply` completo para S1–S16, quinta guía, adjuntos, bandeja, estado de conversación, entrada del motor, metadatos de chunk y evento de auditoría. `supuestos` registra el mínimo de seguro alternativo (P03); el saneado de eventos usa la redacción de P04. | Prompt P01 ítem 5. |
| `contracts/vectors/`: vectores de conformidad de normalización de guía, hash de usuario y PII. | P02 implementa `shared/` y debe reproducirlos (DEC-C17). |
| Manifiesto de mocks: sección `errors` por operación y marca `pendiente_de_servicio_real`. | DEC-C18, DEC-C23. |

## 1.1.0 — 2026-11-03

Alineación con el documento maestro (escenarios §G y decisiones §H). Detalle en [`DECISIONS.md`](DECISIONS.md).

| Cambio | Motivo |
|--------|--------|
| `CalculoFlete` añade `flete_minimo_cop` y `flete_minimo_aplicado`; el flete base no puede quedar bajo el mínimo. | DEC-Q2, escenario S9 (total 22.500). |
| `PbxTurnResponse.speech_text` se valida: sin dígitos, Markdown, URLs ni emoji, y como máximo 40 palabras. | DEC-C16, escenario S16. |
| El ejemplo de siniestro sobre TLX-752190-CO pasa a `ESCALADO` con CRIT-003 → JURIDICO_PQRS. El ejemplo §5.4 del documento (`escalamiento: null`) contradecía S14. | DEC-E2. |
| `EscalationInput.incident_reported`: en `incident_context` un estado de novedad de la guía también es incidente. | DEC-E3, escenario S4. |
| Nuevos artefactos generados: `schemas/` (JSON Schema), `mocks/manifest.json`, `types/api.d.ts`. Nuevo fake `app.contracts.fakes.GoldenConversationService`. | Puerta A: "OpenAPI + modelos + ejemplos + mocks"; tipos TS. |
| 33 ejemplos nuevos: todas las operaciones tienen al menos una respuesta, y quedan cubiertos 16 de los 18 escenarios de la demo. | Mocks de frontend y E2E. |
| Las utilidades (`normalizers`, `pii`, `money`, `timezone`) pasan de `app.shared` a `app.contracts`; el exportador pasa a `python -m app.contracts`. | Propiedad de carpetas del maestro (§B.3); DEC-C17. |

## 1.0.0 — 2026-11-03

Primera materialización del documento de contratos. Diferencias con el texto original:

| Cambio | Tipo | Decisión |
|--------|------|----------|
| `ErrorCode` añade `NO_CONFIGURADO` y `NO_AUTORIZADO`. | aditivo | DEC-C2 |
| `TipoNovedad` añade `SAQUEO`. | aditivo | DEC-C3 |
| `MessageContent` añade `system_event` (obligatorio con `message_type=SYSTEM_EVENT`). | aditivo | DEC-C6 |
| `EscalationInput.sentiment` y `HandoffPayload.sentimiento_detectado` pasan de `str` a `Sentiment`. | aclaración | DEC-C8 |
| `ESCALAR_Urgencia_OPERACIONES` (JSON) se canoniza como `ESCALAR_URGENCIA_OPERACIONES`; el original va en `accion_raw`. | aclaración (prevista) | — |
| `GET /handoffs` y `GET /handoffs/{id}` requieren `X-Admin-Token`. | seguridad | DEC-C13 |
| Se definen las formas que el documento dejaba abiertas. | aditivo | DEC-C6 |
| Tabla código de error → estado HTTP y mensajes por defecto en es-CO. | aditivo | DEC-C1, DEC-C2 |
