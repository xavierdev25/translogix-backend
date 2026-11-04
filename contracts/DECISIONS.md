# Decisiones del contrato (P01)

El registro oficial del proyecto es `02_MATRIZ_DISCREPANCIAS_Y_DECISIONES.md`, que P00 vuelca en
`docs/decisions.md`. **P01 no lo ha recibido.** Este archivo recoge:

1. cómo el contrato implementa cada decisión del proyecto que lo afecta, con los IDs y valores del
   documento maestro (§G escenarios, §H decisiones pendientes) y del Prompt Pack (P02–P09);
2. las decisiones propias de P01 al materializar el contrato (`DEC-C*`).

Cuando P00 publique el registro, la sección 1 se verifica fila por fila contra él.

**Estados:** `CONFIRMADA` (contrato o fuente) · `PROVISIONAL` (el maestro la da por buena pero la
deja abierta en §H) · `INFERIDA` (deducida por P01; validar) · `PENDIENTE` (sin decidir).

## 1. Decisiones del proyecto que el contrato implementa

| ID | Cómo queda en el contrato | Evidencia | Estado |
|----|---------------------------|-----------|--------|
| DEC-Q1 | Seguro = máx(1 % × valor declarado, mínimo); mínimo según `INSURANCE_MIN_SOURCE` (`pdf` = $2.500; `csv` = `seguro_minimo_cop` de la ruta, $3.000–$5.000). Los ejemplos S7 y S9 dan el mismo seguro con cualquiera de los dos (125.000 y 4.500 superan ambos mínimos). | Maestro §H.2; contrato §9. | PROVISIONAL |
| DEC-Q2 | Flete base = máx(flete mínimo de la ruta, peso facturable × tarifa por kg). `CalculoFlete` expone `flete_minimo_cop` y `flete_minimo_aplicado`; el modelo impide un flete base por debajo del mínimo. S9: 18.000. | Maestro §G S9, §H.1. | PROVISIONAL |
| DEC-Q2a | La tarifa por kg es la real si factura el peso real y la volumétrica si factura el volumétrico (`tarifa_aplicada.tipo`). No altera S7 (real) ni S9 (aplica el mínimo). | El CSV trae dos tarifas por ruta. | INFERIDA |
| DEC-Q3 | Recargo de frío = % de la ruta sobre el flete base (S7: 25 % × 65.975 = 16.493,75 → 16.494). Mercancía peligrosa: valor fijo de la ruta (`recargo_mercancia_peligrosa_cop`). | Maestro §H.1; contrato §5.3. | PROVISIONAL |
| DEC-Q4 | Peso real mayor que `peso_maximo_paquete_kg` del destino → advertencia en `advertencias`, no bloqueo. `EXCEDE_LIMITE_COBERTURA` queda disponible si la decisión cambia a bloquear. | Prompt P03. | PROVISIONAL |
| DEC-Q6 | Ruta sin fila en la matriz → `RUTA_SIN_TARIFA` (422); nunca se inventa tarifa. Mensaje "no tengo tarifa para esa ruta en la matriz disponible", nunca "no cubrimos". | Maestro §G S8; prompt P03. | CONFIRMADA |
| DEC-Q8 | COP entero; `Decimal` y `ROUND_HALF_UP` por línea (`app.contracts.money.to_cop`). | Contrato. | CONFIRMADA |
| DEC-T1 | La ETA es siempre la del TMS. El tiempo de entrega de la cotización sale del CSV; si difiere del directorio, se advierte con ambos valores (4 rutas: CLO-BUN-015, BAQ-SMR-016, BAQ-CTG-017, BGA-CUC-018). | Prompts P03, P04. | PROVISIONAL |
| DEC-S1 | Vista segura de tracking. Se impone en los modelos: los textos visibles al cliente (`ClientSafeText`) no aceptan teléfono, placa, cédula, NIT, dirección exacta, GPS, URL, correo ni nombre de persona. | Contrato §5.2; maestro S3, S5. | CONFIRMADA |
| DEC-E2 | `CRIT003_MODE=claim_context`: CRIT-003 dispara solo si `intent == PQRS` y máx(valor reclamado, valor declarado de la guía) > 5.000.000; en `literal`, en cualquier turno en que se conozca el dato. S1 (rastreo, declarado 12,5 M) no escala; S14 (siniestro sobre guía de 85 M) escala a JURIDICO_PQRS. **Corrige el ejemplo §5.4 del documento de contratos** (`escalamiento: null`), que contradecía S14. | Maestro §G S1, S4, S14; §H.3; prompts P07 y P09. | PROVISIONAL |
| DEC-E3 | `CRIT005_MODE=incident_context`: CRIT-005 dispara con mercancía peligrosa **y** (estado de novedad o retenida, intención PQRS o `incident_reported`); rama de frío literal (refrigeración y `RETENIDO_NOVEDAD`). Las consultas documentales previas al envío nunca lo disparan. En `literal`, `mercancia_peligrosa == true` basta. S4 (peligrosa retenida) escala a OPERACIONES/URGENTE; S10 (consulta sin guía) y S1 (frío en tránsito) no. | Maestro §G S4, S10; §H.3; prompt P07. | PROVISIONAL |
| DEC-E4 | `OFRECER_HANDOFF` no crea handoff; `fallback_count` = `intentos_previos`; `INFO-INTENTOS`: `intentos_entrega_fallidos >= 2` ofrece handoff. El dataset no trae ese dato: `TmsShipment.intentos_entrega_fallidos` es opcional y, si falta, la regla no se evalúa. | Contrato §6; reglas JSON; prompt P07. | CONFIRMADA |
| DEC-E7 | Orden del criterio primario: CRIT-005 > CRIT-003 > CRIT-002 > CRIT-004 > CRIT-001 > INFO-INTENTOS, configurable; se registra en `config_snapshot` (`DEFAULT_PRIORITY_ORDER`). | Prompt P07. | PROVISIONAL |
| DEC-CH1 | Canal persistido en conversación y mensajes; base de las métricas por canal. | Contrato §2, §8. | CONFIRMADA |
| DEC-P2 | La elegibilidad cita documento y sección (`Elegibilidad.fundamento`); nunca añade derechos propios. | Prompt P09. | CONFIRMADA |
| DEC-P3 | Liquidación siempre `PRELIMINAR_NO_VINCULANTE`; `flete_devuelto = PENDIENTE_REVISION_HUMANA`. | Prompt P09; contrato §5.4. | CONFIRMADA |
| DEC-P4 | Checklist de siniestro (PQRS §4 + `ACTA_CON_SALVEDAD` del reto). Las tres fotos del PDF se agrupan en `FOTOS_EMPAQUE` y `FOTOS_DANO`, obligatorias solo en avería o saqueo. | Contrato §5.4. | CONFIRMADA |
| DEC-P5 | Calendario de festivos: no se codifica; llega por `BUSINESS_HOLIDAYS_FILE`. Mientras no exista, `Elegibilidad.nota` lo declara ("días hábiles sin considerar festivos"). | Maestro §H.5. | PENDIENTE |
| DEC-R2 | `ChunkMetadata.zona` derivada del departamento. Falta la tabla departamento → zona. | Contrato §7. | PENDIENTE (P05) |
| DEC-ACC1 | Qué cuenta es GOLD para CRIT-004. El contrato ya transporta `tipo_cuenta` y `envios_mensuales_cuenta` en `EscalationInput`; el dato vive en el fixture de P00. | Maestro §H.4. | PENDIENTE |

Las decisiones DEC-E1, E5, E6, P1, R1, R3, R4 y T2 que citan los prompts no cambian la forma del
contrato; su contenido está en `02_MATRIZ…`.

**Renumeración respecto a 1.0.0:** la primera versión de este registro, reconstruida sin el maestro,
llamaba DEC-Q3 al seguro mínimo y DEC-Q2b al flete mínimo, e interpretaba DEC-E2 (comparar solo el
valor reclamado) y DEC-E3 (solo incidente reportado por el usuario) de forma incompatible con S4 y
S14. Quedan corregidas arriba.

## 2. Decisiones propias de P01

| ID | Decisión | Motivo | Estado |
|----|----------|--------|--------|
| DEC-C1 | Un estado HTTP fijo por código de error (`ERROR_HTTP_STATUS`): reglas de negocio y validación → 422; no encontrado → 404; firma → 401; autorización → 403; tasa → 429; dependencia caída o no configurada → 503; interno → 500. | Sin tabla, cada servicio respondería distinto. | CONFIRMADA |
| DEC-C2 | Códigos añadidos `NO_CONFIGURADO` y `NO_AUTORIZADO`; mensaje por defecto en es-CO para cada código. | El contrato usa NO_CONFIGURADO en NullAsr/NullTts sin definirlo. | CONFIRMADA |
| DEC-C3 | `TipoNovedad.SAQUEO` añadido. Plazo de radicación pendiente (PQRS, Caso 2, sugiere 3 días hábiles). | La regla de fotos cita "avería o saqueo". | CONFIRMADA (plazo PENDIENTE) |
| DEC-C4 | `message_id` de PBX = `<call_id>:<turn_index>`. | El PBX no entrega ids y la idempotencia los necesita. | CONFIRMADA |
| DEC-C5 | Hash de usuario: HMAC-SHA256 con `PII_HASH_SALT` (≥ 16 caracteres) del id canónico (ids telefónicos → solo dígitos, así `wa_id` y ANI coinciden; resto → minúsculas). Máscara: últimos 4. | Todos los adaptadores deben producir el mismo hash. | CONFIRMADA |
| DEC-C6 | Formas que el documento no definía: `MessageContent.system_event`, `ChannelOutbound`, `Transcript`, `AudioRef`, `HistorialItem.rol`, `SessionHistory`, `HandoffSummary`, PBX (turno y respuesta), WhatsApp (ack y simulación), `ReadinessResponse`, `ChannelMetricsResponse`, `ReindexRequest/Response`, `QuoteSummaryBlock`, eventos WS. | Sin forma no hay contrato. | PROVISIONAL |
| DEC-C7 | `estado.codigo` del tracking es string abierto; `EstadoGuia` cataloga los conocidos. | Un estado nuevo del TMS no debe romper clientes. | CONFIRMADA |
| DEC-C8 | Sentimiento como enumerado `Sentiment` en todo el contrato. | Dos campos estaban como `str` libre. | CONFIRMADA |
| DEC-C9 | _Retirada en 1.2.0:_ el orden de prioridad es DEC-E7 (prompt P07). | — | — |
| DEC-C10 | `SINIESTRO` exige guía, tipo de novedad y valor reclamado; `deducible_pct` entero; `flete_devuelto` siempre `PENDIENTE_REVISION_HUMANA` en el MVP. | Sin esos datos no hay elegibilidad ni liquidación. | CONFIRMADA |
| DEC-C11 | `POST /api/v1/attachments` (P1, multipart): solo PDF o JPG detectados por contenido; devuelve `attachment_id`, tipo, tamaño y SHA-256. | Chat y PQRS referenciaban `attachment_id` sin endpoint que lo creara; el prompt P01 lo pide. | CONFIRMADA |
| DEC-C12 | `TmsShipment` es interno: tolera campos nuevos, excluye la PII de `repr` y no se publica. | Es el único modelo con PII. | CONFIRMADA |
| DEC-C13 | `GET /handoffs` y `GET /handoffs/{id}` requieren `X-Admin-Token`. | El payload incluye el historial del cliente; el documento solo protegía reindex. | PROVISIONAL (revertible) |
| DEC-C14 | `ultima_temperatura_c` = última temperatura reportada en los eventos (3,8 °C en S1, aunque el evento más reciente no la traiga). | Ejemplo del contrato y S1. | INFERIDA |
| DEC-C15 | Las invariantes se validan en los modelos (totales y mínimo de cotización, deducible, faltantes y estado del ticket, *grounding* RAG, coherencia del escalamiento, metadatos por canal, tipo ↔ contenido, máximo 3 respuestas rápidas, `HANDOFF_NOTICE` ⇒ `ESCALATED`, confianza solo con RAG). | Ninguna implementación puede devolver datos incoherentes. | CONFIRMADA |
| DEC-C16 | `PbxTurnResponse.speech_text`: sin dígitos (se deletrean guías, montos y horas), sin Markdown, URLs ni emoji, y como máximo 40 palabras. El "~40" del documento se fija en 40. | Reglas de renderizado PBX; S16. | PROVISIONAL |
| DEC-C17 | Las utilidades que los modelos necesitan (detección de PII, patrón y normalizador de guía, dinero, zona horaria) tienen una implementación de referencia en `app/contracts/`. P02 implementa las suyas en `shared/` (o reexporta), pero deben pasar los vectores de `contracts/vectors/` (normalización de guía, hash de usuario, PII). | Los modelos no pueden depender de una carpeta ajena; dos hashes de usuario distintos romperían las sesiones. | CONFIRMADA |
| DEC-C18 | Mocks y fakes: `contracts/mocks/manifest.json` (operación → ejemplos con *match*, errores por código, marca `pendiente_de_servicio_real`) y `app.contracts.fakes` (un fake determinista por puerto; `FakeConversationService()` responde a los tres canales). Cubren los 18 escenarios. | Maestro §F; prompt P01 ítems 4 y 5. | CONFIRMADA |
| DEC-C19 | `EstadoTicket.FUERA_DE_PLAZO`. El escalamiento prevalece (`ESCALADO`); si no hay escalamiento, plazo vencido ⇔ `FUERA_DE_PLAZO`. | Prompt P01 (a); P09. | CONFIRMADA |
| DEC-C20 | `PATCH /api/v1/handoffs/{id}` (P1, con token): `ASSIGNED` o `CLOSED`. | P07 pide asignación y cierre para la bandeja y §5 no los tenía. | CONFIRMADA |
| DEC-C21 | `Cop` es entero **estricto**: `125000.0`, `"125000"` o `true` se rechazan. | Prompt P01 ítem 6 ("rechaza dinero no entero"); en modo laxo Pydantic los aceptaba. | CONFIRMADA |
| DEC-C22 | El cuerpo del webhook de WhatsApp es un modelo (`WhatsAppWebhookPayload`) que conserva campos desconocidos; la simulación acepta el mismo cuerpo. Estructura según Meta: validar contra su documentación vigente. | Prompt P01 (c). | PROVISIONAL |
| DEC-C23 | La marca `_pendiente_de_servicio_real` del prompt P01 no va dentro de los JSON (los modelos rechazan campos extra por diseño): vive en el registro de ejemplos y en el manifiesto de mocks. | Coherencia con DEC-C15. | CONFIRMADA |
| DEC-C24 | `CLOCK_OVERRIDE` se suma a los nombres canónicos de entorno; los contratos no leen la hora del sistema (puerto `Clock`, `FixedClock` en fakes). | Bloque común, regla 10. | CONFIRMADA |
| DEC-CH2 | `SESSION_TTL_MINUTES_<CANAL>` sin valor en las fuentes; para WhatsApp revisar la ventana de 24 h en la documentación vigente de Meta. | — | PENDIENTE |

## 3. Dependencias añadidas (bloque común: justificar por escrito)

| Dependencia | Tipo | Requisito que la exige |
|-------------|------|------------------------|
| `fastapi` | ejecución | Stack acordado; deriva el OpenAPI desde los modelos. |
| `pydantic` | ejecución | Stack acordado (modelos canónicos). |
| `tzdata` | ejecución | Zona `America/Bogota` disponible aunque la imagen no traiga la base de zonas horarias. |
| `python-multipart` | ejecución | FastAPI la exige para declarar `POST /attachments` (multipart). |
| `openapi-spec-validator` | desarrollo | Prompt P01 ítem 6: validar el OpenAPI. |
| `jsonschema` (+ stubs) | desarrollo | Validar los ejemplos contra el YAML y los JSON Schema publicados. |
| `pyyaml` (+ stubs) | desarrollo | Escribir y leer `openapi.yaml`. |
| `openapi-typescript@7.13.0` | herramienta (npx, sin instalar) | Tipos del frontend generados desde el OpenAPI (maestro A.5). |

P00 debe copiarlas a `docs/decisions.md` cuando lo cree.

## 4. Pendiente de terceros

- **P00:** publicar el registro (`02_MATRIZ…`) para verificar la sección 1.
- **Equipo (§H del maestro):** DEC-Q1, DEC-Q2/Q3, DEC-E2/E3, DEC-ACC1, DEC-P5; credenciales reales de WhatsApp o telefonía.
- **Sin dueño aún:** DEC-C3 (plazo de saqueo), DEC-R2 (zonas), DEC-CH2 (TTL de sesión).
- **P06 y P08:** sus prompts no venían en el Prompt Pack recibido; si `RagPort`, `SpecialCargoPort` o
  `ConversationService` no les sirven, lo piden en `docs/contract-change-requests.md`.
