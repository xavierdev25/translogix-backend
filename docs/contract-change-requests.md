# Solicitudes de cambio de contrato

Solo **P01** modifica `contracts/` y `backend/app/contracts/`. Cualquier otro agente que necesite
un cambio lo propone aquí; P01 lo acepta o rechaza, actualiza los modelos, regenera los artefactos
(`uv run python -m app.contracts` y `contracts/types/generate.sh`) y registra la versión en
`contracts/CHANGELOG.md`.

> Este archivo vive en `docs/` (carpeta de P00) porque el contrato lo define como canal de cambios.
> P00 lo adopta tal cual.

## Cómo proponer

Añade una entrada al final del registro con este formato:

```markdown
### CR-NNN — <título corto>

- **Solicitante:** P0X
- **Fecha:** AAAA-MM-DD
- **Qué cambia:** modelo, campo o endpoint afectado y forma propuesta (JSON o Pydantic).
- **Por qué:** caso de uso que hoy no se puede cumplir.
- **Compatibilidad:** aditivo (MINOR) o rompe clientes (MAJOR). Si rompe, a quién afecta.
- **Ejemplo:** payload antes / después.
- **Estado:** PROPUESTA | ACEPTADA (versión X.Y.Z) | RECHAZADA (motivo)
```

## Reglas

1. Mientras no se acepte, se implementa contra la versión vigente; no se añaden campos "por adelantado".
2. Un cambio aceptado entra con su ejemplo en `contracts/examples/` y sus pruebas.
3. Los cambios MAJOR se avisan a todos los agentes antes de publicarse.

## Notas de coordinación de P01 (no son solicitudes de cambio)

- **Para P00 — `data/source/`:** P01 creó la copia de las 7 fuentes recibidas para poder probar contra
  datos reales, sin modificar su contenido (`SHA256SUMS`; P00 lo sustituye por `MANIFEST.sha256`).
  `asd.md` conserva su nombre. Falta el **PDF del dosier** (P00 espera 8 archivos) y el manual se
  llama `manual_de_mercanc_as_peligrosas_y_embalaje.md`, no `…mercancias…` como dice el bloque común.
- **Para P00 — `docs/decisions.md`:** P01 no lo crea. Las decisiones del contrato están en
  `contracts/DECISIONS.md`; su sección 1 debe verificarse contra `02_MATRIZ_DISCREPANCIAS_Y_DECISIONES.md`.
- **Para P00 — `.env.example`:** semilla de P01 con los nombres canónicos (§9 + `CLOCK_OVERRIDE`);
  P00 lo adopta. Una prueba de contrato compara sus claves con `EnvVar`.
- **Para P00 — dependencias:** copiar a `docs/decisions.md` la tabla de dependencias justificadas de
  `contracts/DECISIONS.md` §3.
- **Para P02 — `backend/pyproject.toml`, `backend/uv.lock`:** semilla de P01 para ejecutar las pruebas
  del contrato. P02 los adopta; conservar las dependencias de desarrollo de `tests/contracts/`
  (pytest, pytest-asyncio, pyyaml, jsonschema, openapi-spec-validator y sus stubs) y `python-multipart`.
- **Para P02 — `shared/`:** `hash_external_id`, `normalize_guia` y el filtro de PII son de P02, pero deben
  reproducir exactamente `contracts/vectors/hash_usuario.json`, `normalizacion_guia.json` y `pii.json`
  (la implementación de referencia está en `app.contracts.pii` y `app.contracts.normalizers`; puede
  reexportarse). Dos hashes de usuario distintos romperían la resolución de sesiones (DEC-C17). Para
  mapear errores a HTTP, usar `app.contracts.common.ERROR_HTTP_STATUS`.

## Registro

Contexto común de CR-002 a CR-007: la especificación técnica 1.1 (evolución comercial y logística) y
la solicitud del CRM del asesor (front, rama `kenith-crm`). Los huecos, los conflictos con el P0 y las
decisiones D1–D14 están en [`EVOLUCION_1_1_REVISION.md`](EVOLUCION_1_1_REVISION.md); la
reconciliación del CRM y las decisiones D-CRM1–9, en
[`REVISION_SOLICITUD_ASESOR.md`](REVISION_SOLICITUD_ASESOR.md).

**Convenciones comunes de CR-002 a CR-007** (ajustadas al registrar CR-007):
- Rutas de personal interno, que solo aceptan el esquema `StaffToken` (CR-002):
  - `/api/v1/advisor/*` para el CRM (ASESOR, SUPERVISOR);
  - `/api/v1/auth/*` para el login;
  - `/api/v1/internal/*` para la operación logística y la revisión (CR-005, CR-006).
- Las rutas B2B aceptan solo `ApiKey`. Ninguna credencial sirve en la otra familia de rutas.
- Errores con el sobre vigente.
- **Concurrencia optimista única:** toda entidad con etapas o estados lleva `version` (entero) y todo
  cambio envía `expected_version`. Una discrepancia responde 409 `VERSION_DESACTUALIZADA`. Así se evita
  perder actualizaciones concurrentes.

Códigos de error nuevos que comparten:

| Código | HTTP | Uso |
|---|---|---|
| `TRANSICION_NO_PERMITIDA` | 422 | El estado destino no es alcanzable desde el actual |
| `VERSION_DESACTUALIZADA` | 409 | `expected_version` no coincide con la versión actual (sustituye a `ESTADO_DESACTUALIZADO`) |
| `JUSTIFICACION_REQUERIDA` | 422 | Falta `reason` en una transición que la exige |
| `DATOS_INCOMPLETOS` | 422 | Faltan datos obligatorios para avanzar |
| `CONFIRMACION_AGENCIA_REQUERIDA` | 422 | `EN_CURSO` sin una recepción registrada de la agencia |
| `REVISION_PENDIENTE` | 422 | El envío no puede avanzar mientras su revisión esté abierta |
| `ESTADO_SIN_CAMBIO` | 409 | Se pidió el mismo estado logístico actual |

### CR-001 — Envíos masivos B2B (registro retroactivo)

- **Solicitante:** especificación técnica P0 de envíos masivos (aprobada).
- **Fecha:** 2026-11-04
- **Qué cambia:** `POST /api/v1/shipment-batches`, `GET …/{batch_id}`, `GET …/{batch_id}/items`, el
  esquema `ApiKey`, 7 códigos de error y 4 `EnvVar`. Detalle en `contracts/CHANGELOG.md` 1.3.0.
- **Por qué:** se aplicó directamente desde la especificación P0 aprobada. Se registra aquí para que
  el registro quede completo, como pide la especificación 1.1 §10.
- **Compatibilidad:** aditivo (MINOR).
- **Ejemplo:** `contracts/examples/shipment-batch.*.json`.
- **Estado:** ACEPTADA (1.3.0)

### CR-002 — Identidades internas: usuarios, roles y token personal

- **Solicitante:** especificación 1.1 §5.3, §7.1 y §13.8.
- **Fecha:** 2026-11-04
- **Qué cambia:**
  - **Tablas:**
    - `staff_users`: `id`, `email` (UNIQUE), `nombre`, `roles`, `estado` (ACTIVE/INACTIVE), `created_at`, `updated_at`.
    - `staff_tokens`: `id`, `staff_user_id` (FK), `key_prefix` (UNIQUE), `key_hash` (SHA-256),
      `expires_at`, `last_used_at`, `revoked_at`.
  - **Token:** `tlxs_<prefijo>_<secreto>`, enviado como `Authorization: Bearer`, esquema OpenAPI
    `StaffToken`. Se distingue de la API key B2B (`tlx_…`) por el prefijo y por el esquema de cada ruta.
  - **Roles:** `ASESOR`, `SUPERVISOR`, `OPERADOR_LOGISTICO` y `REVISOR_ESPECIALIZADO`.
  - **Administración:** el alta, la baja y la rotación de usuarios y tokens se hacen por CLI
    (`python -m app.staff.cli`), sin endpoints nuevos.
  - **Auditoría:** `api_audit_events` añade la columna `staff_user_id`, nullable (migración aditiva).

  | Operación | ASESOR | SUPERVISOR | OPERADOR_LOGISTICO | REVISOR_ESPECIALIZADO |
  |---|---|---|---|---|
  | Ver gestiones comerciales | Las suyas y las sin asignar | Todas | — | — |
  | Autoasignarse una gestión sin asignar | Sí | Sí | — | — |
  | Reasignar a otro asesor | — | Sí | — | — |
  | Registrar contactos y transiciones comerciales | Si es el asignado | Sí | — | — |
  | Registrar entrega a agencia | Si es el asignado | Sí | — | — |
  | Registrar recepción de la agencia | — | Sí | Sí | — |
  | Cambiar el estado logístico | — | Sí | Sí | — |
  | Aprobar o rechazar una revisión de mercancía | — | — | — | Sí |
- **Por qué:** hoy solo existe un `ADMIN_TOKEN` compartido. Con él no se puede saber qué persona hizo
  cada cambio, que es lo que exigen `performed_by` y `changed_by`.
- **Compatibilidad:** aditivo (MINOR). Las rutas existentes y `ADMIN_TOKEN` no cambian.
- **Ejemplo:** `Authorization: Bearer tlxs_3f9a1c0b7d2e_…`. Un token de un usuario INACTIVE, revocado o
  vencido responde 401; un rol insuficiente, 403.
- **Decisión requerida:** D10 (SSO o cuentas locales); de momento, cuentas locales (DEC-STF4).
- **Implementación:**
  - código en `app/staff/`, la dependencia `staff_authenticated(*roles)` y el CLI `app.staff.cli`;
  - migración 0003;
  - 15 pruebas en `tests/staff/` (DEC-STF1–5).

  La matriz de permisos por operación se aplica en cada caso de uso de CR-004, CR-005 y CR-006 cuando
  esos CR se acepten.
- **Estado:** ACEPTADA (1.4.0)

### CR-003 — Motor tarifario por perfiles versionados (TEST_ONLY / COMMERCIAL)

- **Solicitante:** especificación 1.1 §2, §5.1–5.2, §6 y §13.1–13.3.
- **Fecha:** 2026-11-04
- **Qué cambia:**
  - **Tabla `tariff_profiles`:** `id`, `name`, `version`, `environment_type` (TEST_ONLY/COMMERCIAL),
    `status` (ACTIVE/INACTIVE), `created_at`, `effective_from`, `effective_until`; UNIQUE(`name`, `version`).
    Un perfil activado es **inmutable**: cambiar una regla obliga a crear una versión nueva.
  - **Tabla `tariff_rules`:** `id`, `tariff_profile_id` (FK), `destination_city_code` (DANE),
    `base_price_cop` (BIGINT), `included_weight_kg` (NUMERIC(10,2)), `excess_price_per_kg_cop` (BIGINT),
    `created_at`; UNIQUE(`tariff_profile_id`, `destination_city_code`).
  - **Semilla:** el perfil `matriz-pruebas` v1 (TEST_ONLY) con las 5 reglas de la 1.1 §6.2, cargado por
    la migración. No va escrito en el código de `QuotationService`.
  - **`EnvVar.TARIFF_ENVIRONMENT`** (`TEST_ONLY` por defecto): el despliegue solo puede usar perfiles de
    ese tipo. Un perfil TEST_ONLY nunca produce `precio_comercial: true`.
  - **Fórmula `excedente-v1`:** `T = B + max(0, P − L) × A`, calculada con `Decimal` y peso exacto, sin
    redondear el peso. El importe se redondea a COP entero con `ROUND_HALF_UP` (D3).
  - **`POST /api/v1/cotizaciones`** (nuevo): calcula y persiste el snapshot. Con CR-004, además abre la
    gestión comercial. `POST /cotizar` (`provisional-v1`) **no cambia**.

  > **Ajuste al aceptar:** por D4 la petición lleva las medidas, y la respuesta trae el peso
  > volumétrico, el facturable y el IVA (D5). Forma final: `contracts/examples/cotizaciones.*.json`.

  ```json
  // request (versión propuesta; ver el ajuste)
  {"destino": "Barranquilla", "peso_kg": 6.2}
  // response 201
  {"cotizacion_id": "uuid", "destino": {"codigo_dane": "08001", "municipio": "Barranquilla"},
   "peso_kg": 6.2, "formula_version": "excedente-v1", "precio_comercial": false,
   "tarifa": {"perfil_id": "uuid", "perfil_nombre": "matriz-pruebas", "perfil_version": 1,
              "tipo_perfil": "TEST_ONLY", "base_cop": 28000, "peso_incluido_kg": 5.5,
              "peso_excedente_kg": 0.7, "precio_kg_excedente_cop": 5000},
   "total_cop": 31500, "calculado_en": "2026-11-04T10:00:00-05:00"}
  ```
  - **Errores:** `CIUDAD_NO_RECONOCIDA`, `TARIFA_NO_DISPONIBLE` (destino sin regla), `NO_CONFIGURADO` (no
    hay un perfil activo y vigente del tipo permitido) y `VALIDACION` (peso ≤ 0 o con más de 2 decimales).
  - **Casos verificados con la matriz de prueba** (`Decimal`, COP):

  | Destino | 3 kg | = L | L + 0,01 | 6,2 kg | 7,25 kg | 50 kg |
  |---|---:|---:|---:|---:|---:|---:|
  | Barranquilla | 28.000 | 28.000 | 28.050 | **31.500** | 36.750 | 250.500 |
  | Bucaramanga | 19.300 | 19.300 | 19.336 | 23.620 | 27.400 | 181.300 |
  | Cali | 19.800 | 19.800 | 19.837 | 24.240 | 28.125 | 186.300 |
  | Cartagena | 26.900 | 26.900 | 26.948 | 30.260 | 35.300 | 240.500 |
  | Cúcuta | 23.800 | 23.800 | 23.844 | 29.080 | 33.700 | 221.800 |

  Todas las `A` son múltiplos de 100, así que con 0,01 kg el importe siempre es entero. Con 0,001 kg
  dejaría de serlo: Cali, 5,001 kg → 19.803,7 → 19.804.
- **Por qué:** las tarifas actuales son solo de prueba y se debe aplicar la fórmula aprobada, guardando
  la versión del perfil (§6.5).
- **Compatibilidad:** aditivo (MINOR): un endpoint, modelos y una `EnvVar` nuevos. `/cotizar` y el chat
  siguen como están. Sustituir `/cotizar` sería MAJOR y no se propone.
- **Pruebas (1.1 §11):** peso por debajo de `L`, igual, inmediatamente por encima, con fracciones y alto;
  destino sin regla; perfil inactivo o fuera de vigencia; perfil COMMERCIAL rechazado en modo TEST_ONLY;
  snapshot intacto tras crear la versión 2 del perfil.
- **Decisiones (respondidas el 2026-11-04):**
  - D2: el excedente empieza justo después de `L`.
  - D3: 2 decimales.
  - D4: peso facturable para todos los destinos.
  - D5: IVA 19 % encima, por ahora, sin seguro ni recargos.
  - El origen no influye en el precio.

  Detalle en DEC-TAR1 a DEC-TAR7.
- **Implementación:**
  - `app/tariffs/` (motor puro, repositorios, servicio y CLI);
  - migración 0004, con la semilla `matriz-pruebas` v1;
  - 51 pruebas en `tests/tariffs/`.
- **Estado:** ACEPTADA (1.5.0)

### CR-004 — Gestión comercial: seguimiento del asesor y entrega a agencia

> **Sustituida por CR-007.** La gestión comercial pasa a ser el lead del CRM del asesor. Las tablas,
> la máquina de estados y la entrega a agencia de esta propuesta se reutilizan allí, con las rutas
> `/api/v1/advisor/*` y `expected_version`. Se conserva el texto como antecedente.

- **Solicitante:** especificación 1.1 §3.2, §4 (etapas B y C), §5.3–5.5 y §13.4–13.6.
- **Fecha:** 2026-11-04
- **Qué cambia:**
  - **Tablas:**
    - `advisor_followups`: `id`, `quotation_id` (FK a CR-003), `assigned_advisor_id` (FK `staff_users`,
      nullable), `status`, `shipment_id` (nullable), `next_action_at`, `last_contact_at`, `created_at`,
      `updated_at`.
    - `advisor_followup_events`: solo inserción; `id`, `followup_id`, `event_type` (CONTACTO, NOTA,
      SOLICITUD_DATOS, PROGRAMACION, TRANSICION, ASIGNACION), `result`, `notes`, `performed_by`,
      `created_at`.
    - `followup_contacts`: PII del cliente que se contacta, separada como `shipment_recipients`.
    - `agency_handoffs`: los campos de la 1.1 §5.5 más `handoff_status` (ENTREGADO, RECIBIDO, RECHAZADO).
    - `agencies` (catálogo): opcional mientras `agency_id` sea nullable.
  - **Notas:** se guardan pasadas por `redact_pii`, con un máximo de 1.000 caracteres. Las notas no se
    devuelven nunca a clientes B2B.
  - **Máquina de estados comercial.** `reason` es obligatorio en las transiciones marcadas con ✱:

  | Desde | Hacia |
  |---|---|
  | `PENDIENTE_ASESOR` | `EN_SEGUIMIENTO`, `CANCELADO`✱ |
  | `EN_SEGUIMIENTO` | `DATOS_PENDIENTES`, `LISTO_PARA_AGENCIA`, `CERRADO`✱, `CANCELADO`✱ |
  | `DATOS_PENDIENTES` | `EN_SEGUIMIENTO`, `LISTO_PARA_AGENCIA`, `CANCELADO`✱ |
  | `LISTO_PARA_AGENCIA` | `ENTREGADO_A_AGENCIA` (solo con una entrega registrada), `DATOS_PENDIENTES`✱, `CANCELADO`✱ |
  | `ENTREGADO_A_AGENCIA` | `CERRADO` (automático cuando el envío llega a un estado logístico terminal, o manual por un SUPERVISOR✱) |
  | `CERRADO`, `CANCELADO` | — (terminales) |
  - **Reglas de transición:**
    - **Pasar a `LISTO_PARA_AGENCIA`** exige datos completos (destinatario, medidas, peso y cotización);
      si faltan, `DATOS_INCOMPLETOS`. En la misma transacción se crea el `Shipment`: guía de la secuencia,
      destinatario en `shipment_recipients` y estado logístico `PENDIENTE_RECEPCION`.
    - **Registrar la entrega** cambia la gestión a `ENTREGADO_A_AGENCIA` y **no** toca el estado
      logístico (1.1 §4 C).
  - **Endpoints internos** (todos con `StaffToken`; permisos según la tabla de CR-002):

  | Método y ruta | Propósito |
  |---|---|
  | `GET /api/v1/internal/followups?status=&assigned_to=me&cursor=` | Bandeja del asesor |
  | `GET /api/v1/internal/followups/{id}` | Detalle con eventos, sin PII salvo para el asesor asignado |
  | `POST /api/v1/internal/followups/{id}/assignment` | Asignar, reasignar o autoasignar (`{"advisor_id"}`) |
  | `POST /api/v1/internal/followups/{id}/events` | Contacto, nota, solicitud de datos o próxima acción |
  | `PUT /api/v1/internal/followups/{id}/agency-data` | Datos que necesita la agencia (destinatario, medidas) |
  | `POST /api/v1/internal/followups/{id}/transitions` | `{"to", "expected_status", "reason"}` |
  | `POST /api/v1/internal/followups/{id}/agency-handoffs` | `{"agency_id", "external_reference", "handed_off_at", "notes"}` |
  | `POST /api/v1/internal/agency-handoffs/{id}/reception` | `{"received_at", "external_reference"}`: es lo que habilita `EN_CURSO` (CR-005) |
- **Por qué:** la cotización debe generar un seguimiento del asesor y la entrega a la agencia debe quedar
  registrada aparte de la recepción (1.1 §13.4–13.6).
- **Compatibilidad:** aditivo (MINOR).
- **Pruebas (1.1 §11):**
  - asignación y cambio de responsable;
  - contacto y próxima acción;
  - datos incompletos;
  - paso a `LISTO_PARA_AGENCIA` con creación atómica del envío;
  - entrega a agencia sin cambiar el estado logístico;
  - cancelación con y sin `reason`;
  - transiciones inválidas;
  - `expected_status` desactualizado;
  - asesor no asignado → 403;
  - token B2B en una ruta interna → 401.
- **Decisiones requeridas:**
  - D6: significado de entrega y recepción.
  - D12: qué cotizaciones abren seguimiento.
  - De dónde salen los datos de contacto del cliente.
  - Si los lotes B2B entran en este flujo (conflicto 3 de la revisión).
- **Estado:** RECHAZADA (sustituida por CR-007, que integra la gestión comercial en el CRM del asesor)

### CR-005 — Gestión logística: estados, historial y actualización manual

- **Solicitante:** especificación 1.1 §3.3, §4 (etapa D), §5.6, §7 y §13.7–13.10.
- **Fecha:** 2026-11-04
- **Qué cambia:**
  - **Envíos:**
    - `shipments.logistic_status` es una columna nueva. Los envíos P0 existentes se rellenan con
      `PENDIENTE_RECEPCION`.
    - `shipments.estado` (`REGISTRADO`) se conserva como estado de registro: los dominios no se mezclan
      en un solo campo (1.1 §3).
  - **Historial:** `shipment_status_history` con los campos de la 1.1 §5.6, más `idempotency_key`
    (nullable) y UNIQUE(`shipment_id`, `idempotency_key`). Es solo inserción: no existe ninguna operación
    de actualización ni de borrado.
  - **Máquina de estados logística.** `reason` es obligatorio (con un mínimo de 20 caracteres en
    `PERDIDO`) en las transiciones marcadas con ✱:

  | Desde | Hacia |
  |---|---|
  | `PENDIENTE_RECEPCION` | `EN_CURSO` (exige una recepción de agencia registrada; si no, `CONFIRMACION_AGENCIA_REQUERIDA`), `CANCELADO`✱ |
  | `EN_CURSO` | `EN_TRANSITO`, `EN_REPARTO`, `NOVEDAD`, `RETENIDO`, `PERDIDO`✱, `CANCELADO`✱ |
  | `EN_TRANSITO` | `EN_REPARTO`, `NOVEDAD`, `RETENIDO`, `PERDIDO`✱, `DEVUELTO`✱ |
  | `EN_REPARTO` | `ENTREGADO`, `NOVEDAD`, `PERDIDO`✱, `DEVUELTO`✱ |
  | `NOVEDAD` | `EN_TRANSITO`, `EN_REPARTO`, `RETENIDO`, `ENTREGADO`, `PERDIDO`✱, `DEVUELTO`✱ |
  | `RETENIDO` | `EN_TRANSITO`, `EN_REPARTO`, `PERDIDO`✱, `DEVUELTO`✱ |
  | `ENTREGADO`, `PERDIDO`, `DEVUELTO`, `CANCELADO` | — (terminales; solo un SUPERVISOR puede registrar una corrección con justificación) |
  - **Reglas de cada cambio:**
    - **Atomicidad:** la fila del envío se bloquea (`SELECT … FOR UPDATE`) y se valida
      `expected_version` contra `shipments.status_version` (si no coincide, 409
      `VERSION_DESACTUALIZADA`; ajuste de CR-007). La inserción en el historial y la actualización de `logistic_status` van en la
      misma transacción (1.1 §7.9).
    - **Fechas:** se guarda `occurred_at`, declarado por quien registra, y `recorded_at`, que pone el
      servidor. `occurred_at` no puede estar en el futuro ni antes de la creación del envío.
    - **Eventos atrasados:** un `occurred_at` anterior al último evento se acepta y se marca como
      registrado tarde. El estado actual sigue el orden de registro (D13).
    - **Duplicados:** pedir el estado actual responde `ESTADO_SIN_CAMBIO`. Un reintento con el mismo
      `Idempotency-Key` devuelve el evento original, sin crear otro.
    - **`PERDIDO`:** se audita como incidencia crítica.
  - **Endpoints:**

  | Método y ruta | Quién | Propósito |
  |---|---|---|
  | `POST /api/v1/internal/shipments/{numero_guia}/status-changes` | OPERADOR_LOGISTICO, SUPERVISOR | `{"new_status", "expected_version", "occurred_at", "reason", "notes"}` → 201; `source` siempre `MANUAL` en esta etapa |
  | `GET /api/v1/internal/shipments/{numero_guia}/status-history` | Personal interno | Historial completo |
  | `GET /api/v1/shipments/{numero_guia}` | Cliente B2B propietario (`ApiKey`, scope `shipments:read`) | Estado actual e historial visible: estado y `occurred_at`, sin notas, motivos ni `changed_by` (D11). Un envío de otro cliente responde 404 |
  - **Tracking público:** `GET /tracking/{guia}` no cambia y sigue sin exponer guías internas
    (DEC-SHP8). No hay actualización pública por número de guía (1.1 §7).
- **Por qué:** la actualización logística es manual, la hace personal autorizado y debe dejar trazabilidad
  inmutable (1.1 §7 y §13.7–13.10).
- **Compatibilidad:** aditivo (MINOR): una columna con valor por defecto, tablas y endpoints nuevos.
- **Pruebas (1.1 §11):**
  - transiciones válidas e inválidas, una por fila de la tabla;
  - cambio sin confirmación de agencia → 422;
  - API key B2B en una ruta interna → 401, y operador intentando una revisión → 403;
  - historial completo y ordenado;
  - estado repetido y reintento idempotente;
  - `PERDIDO` sin `reason` o con uno corto;
  - evento atrasado;
  - dos actualizaciones concurrentes del mismo envío: una aplica y la otra responde 409;
  - aislamiento B2B.
- **Decisiones requeridas:**
  - D7: lista definitiva de estados.
  - D11: qué ve el cliente B2B.
  - D13: si un evento atrasado puede cambiar el estado actual.
  - Si `PERDIDO` debe abrir además una escalación a OPERACIONES.
- **Estado:** PROPUESTA

### CR-006 — Ajustes del P0: mercancía peligrosa, cadena de frío y tarifas de prueba

- **Solicitante:** especificación 1.1 §2 y §8.
- **Fecha:** 2026-11-04
- **Qué cambia:**
  - **Mercancía peligrosa: sin aceptación automática.**
    - El envío se sigue creando, con su guía, pero marcado `review_status = PENDIENTE` y
      `review_reason = MERCANCIA_PELIGROSA`.
    - No puede pasar a `EN_CURSO` (`REVISION_PENDIENTE`) hasta que un REVISOR_ESPECIALIZADO lo apruebe:
      `POST /api/v1/internal/shipments/{numero_guia}/review` con `{"decision": "APROBADO|RECHAZADO", "notes"}`.
      Un rechazo lleva el envío a `CANCELADO` con justificación.
    - `ShipmentBatchItemResult` añade `requiere_revision: bool` y `motivos_revision: []`.
    - Alternativa más simple: rechazar esos ítems con `REVISION_ESPECIALIZADA_REQUERIDA`. **No se
      recomienda:** cambia el resultado de ítems que hoy se crean, así que rompe a los clientes.
  - **Cadena de frío.**
    - `EnvioLoteItem` añade `temperatura_requerida_c`, opcional (`{"min", "max"}`, en °C).
    - El directorio solo dice si hay servicio refrigerado; no trae rangos de temperatura. Por eso todo
      envío con frío queda `requiere_revision` con el motivo `CADENA_FRIO_SIN_EVIDENCIA`, y nunca se
      confirma la capacidad.
    - Si el destino no tiene refrigeración, sigue el rechazo actual (`SERVICIO_NO_DISPONIBLE`).
  - **Tarifas de prueba.**
    - `ShipmentBatchItemResult` añade `tipo_perfil: "TEST_ONLY"` y `precio_comercial: false`, para que no
      se lea como precio aprobado.
    - Cuando entre CR-003, los lotes pasan a cotizarse con el perfil TEST_ONLY y el snapshot ampliado.
- **Por qué:** el P0 acepta hoy la mercancía peligrosa sin revisión (DEC-SHP7), lo que contradice la 1.1
  §8, y la cadena de frío no registra temperatura.
- **Compatibilidad:** aditivo (MINOR): campos de salida y un campo de entrada opcional. Ningún ítem que
  hoy se crea pasa a rechazarse.
- **Ejemplo:** el ítem 1 de `shipment-batch.response.json` con `"mercancia_peligrosa": true` añade
  `"requiere_revision": true`, `"motivos_revision": ["MERCANCIA_PELIGROSA"]`, `"tipo_perfil": "TEST_ONLY"` y
  `"precio_comercial": false`.
- **Decisión requerida:** D8 (política de aceptación y documentos exigidos).
- **Estado:** PROPUESTA

### CR-007 — CRM del asesor (sustituye a CR-004)

- **Solicitante:** front del asesor (rama `kenith-crm`): `solicitud-backend-asesor.md` y
  `asesor-backend-faltantes.md`. La reconciliación con el backend está en
  [`REVISION_SOLICITUD_ASESOR.md`](REVISION_SOLICITUD_ASESOR.md).
- **Fecha:** 2026-11-04
- **Versión destino:** contrato **2.1.0** y migración **0008** (`down_revision="0007"`), en dos entregas.
  Re-asignada otra vez: 2.0.0 la tomó CR-010 (remediación prioritaria).
  Re-asignada dos veces: 1.6.0 la tomó CR-008 y 1.7.0/0005, CR-009 (RBAC). Los roles del CRM pasan a ser
  ADMIN y REVIEWER.
  (P0a y P0b). P1 va en otro CR.
- **Qué cambia:**

  **1. Autenticación del asesor (extiende CR-002; no se crea `advisors` ni JWT).**
  - `staff_users` añade `password_hash` (`hashlib.scrypt` de la librería estándar), `availability`
    (DISPONIBLE/OCUPADO), `availability_at` y `last_login_at`. La contraseña se fija por CLI
    (`app.staff.cli set-password`); no hay registro público.
  - `POST /api/v1/auth/login` con `{email, password}` devuelve
    `{access_token, expires_in, asesor: {id, nombre, iniciales, roles, disponibilidad}}`.
    - `access_token` es un token `tlxs_` de **sesión de 12 h** (tabla `staff_tokens`), así que lo valida
      la misma dependencia `staff_authenticated`.
    - Un fallo responde siempre 401 `NO_AUTENTICADO`, sin revelar si el correo existe, con límite de
      intentos por correo y por IP (429).
  - `POST /api/v1/auth/logout` revoca el token en uso.
  - `GET /api/v1/advisor/me` y `PUT /api/v1/advisor/me/availability`.
  - `/handoffs*` acepta también el token de un SUPERVISOR o ASESOR, además de `X-Admin-Token`, para que
    el front deje de usar el token administrativo.

  **2. Captura de contacto en el bot** (orquestador; sin cambio de contrato salvo el último punto):
  - **Cuándo:** al completar una cotización y al escalar. Se evalúa **antes** de `_still_waiting` y de la
    detección de intención.
  - **Orden:** consentimiento (Ley 1581 de 2012) → **teléfono** (obligatorio, normalizado a E.164 `+57`,
    celular `3xx` o fijo `60x`) → **correo** (obligatorio) → nombre y empresa o NIT (opcional; define B2B
    o B2C). Máximo 2 reintentos por campo.
  - **Persistencia:** el texto entrante de esos turnos se guarda redactado (`[TELÉFONO]`, `[CORREO]`).
  - **Textos al cliente:**
    - "Un asesor te llamará al ···4567" sustituye a "Te responderá en este mismo chat" (R1).
    - El `mensaje_cliente` del `HANDOFF_NOTICE` se sobrescribe en el orquestador; el JSON fuente no se
      edita.
  - **Contrato:** `ChatMessageRequest.interactive_reply_id`, opcional (MINOR; resuelve R2).

  **3. Datos (migración 0005).**

  | Tabla | Contenido |
  |---|---|
  | `contacts` | `external_user_id_hash`, `phone_enc`, `phone_hash` (UNIQUE), `phone_last4`, `email_enc`, `email_hash`, `full_name`, `company_name`, `nit_enc`, `nit_hash`, `segment` (B2B/B2C), `phone_verified_at` (primera llamada contestada; no es OTP), `consent_at`, `consent_version` |
  | `leads` (= gestión comercial de la spec 1.1) | `contact_id`, `session_id`, `quote_id` (→ `quotes`) **o** `tariff_quotation_id` (→ `tariff_quotations`), con `CHECK` de exactamente uno (C4); `origin` (COTIZACION/SOLICITUD_ASESOR), `pipeline` (VENTAS/SEGUIMIENTO), `stage`, `owner_id` (→ `staff_users`), `valor_cotizado_cop`, `shipment_id`, `discard_reason`, `sla_due_at`, `first_contact_at`, `version`, `stage_changed_at` |
  | `handoffs` (ALTER, los "casos") | `case_number` (secuencia `crm_case_seq`, que se muestra como `CAS-0042`), `stage` (ESCALADO_IA/EN_GESTION/VALIDANDO_OPS/RESUELTO; `status` se mantiene sincronizado para no romper el contrato), `contact_id`, `owner_id`, `prioridad_crm`, `sla_due_at`, `first_response_at`, `resolved_at`, `version` y `updated_at`, con backfill |
  | `crm_events` | Timeline y auditoría: NOTE, CALL (CONTESTO/NO_CONTESTO/BUZON), WA_ME, STAGE_CHANGE, GUIDE_CREATED, PII_VIEW, SYSTEM; `undo_of` |
  | `crm_tasks` | LLAMADA y RECORDATORIO, con `due_at` y `done_at` |
  | `notifications` | Una fila por asesor, con `read_at` |
  | `agency_handoffs` | Heredada de CR-004: entrega a agencia separada de la recepción |
  | `shipments` (ALTER) | `client_id` nullable y `lead_id` (FK), con `CHECK (client_id IS NOT NULL OR lead_id IS NOT NULL)` |
  | `shipment_recipients` | Cifrado con el mismo esquema que `contacts` |

  - **Cifrado de PII (resuelve D1):** Fernet con `PII_ENCRYPTION_KEY`, rotable con `MultiFernet` y un
    `key_id`; HMAC con `PII_HASH_SALT`. Dependencia nueva: `cryptography`.
  - **Variable de entorno nueva:** `PII_ENCRYPTION_KEY` (secreta).

  **4. Etapas del tablero de ventas y equivalencia con la spec 1.1** (pendiente de D-CRM2). `motivo` es
  obligatorio en las transiciones marcadas con ✱:

  | Etapa CRM | Estado 1.1 | Hacia |
  |---|---|---|
  | `NUEVA_COTIZACION` | `PENDIENTE_ASESOR` | `EN_CONTACTO`, `DESCARTADO`✱ |
  | `EN_CONTACTO` | `EN_SEGUIMIENTO` | `DATOS_PENDIENTES`, `VENTA_CERRADA`, `DESCARTADO`✱ |
  | `DATOS_PENDIENTES` | `DATOS_PENDIENTES` | `EN_CONTACTO`, `VENTA_CERRADA`, `DESCARTADO`✱ |
  | `VENTA_CERRADA` | `LISTO_PARA_AGENCIA` | `ENTREGADO_A_AGENCIA` |
  | `ENTREGADO_A_AGENCIA` | `ENTREGADO_A_AGENCIA` | `CERRADO` (automático cuando el envío llega a un estado logístico terminal) |
  | `DESCARTADO`, `CERRADO` | `CANCELADO`, `CERRADO` | — (terminales) |

  - **`VENTA_CERRADA`** exige el destinatario (formulario de cierre, D-CRM4). En la misma transacción crea
    el `Shipment` con su guía (`next_guia()`) y el snapshot de precio.
  - **Deshacer:** ventana de 30 s, solo el mismo asesor, y **nunca después de `VENTA_CERRADA`** (C7). Un
    envío emitido se anula por CR-005 (`CANCELADO` con justificación).
  - **Casos:** `ESCALADO_IA` → `EN_GESTION` ⇄ `VALIDANDO_OPS` → `RESUELTO`.

  **5. Endpoints** (`StaffToken`; un ASESOR ve lo suyo y lo no asignado, un SUPERVISOR todo):

  | Entrega | Método y ruta |
  |---|---|
  | P0a | `POST /api/v1/auth/login` · `POST /api/v1/auth/logout` · `GET /api/v1/advisor/me` · `PUT /api/v1/advisor/me/availability` |
  | P0a | `GET /api/v1/advisor/boards/{ventas\|casos}` (`?owner=me\|all&q=`): etapas con `count` y `cop_proyectado`, tarjetas y `server_time` |
  | P0a | `PATCH /api/v1/advisor/leads/{id}/stage` · `PATCH /api/v1/advisor/cases/{id}/stage` (`{stage, expected_version, motivo?}`) · `POST /api/v1/advisor/stage-changes/{id}/undo` |
  | P0a | `GET /api/v1/advisor/leads/{id}` · `GET /api/v1/advisor/cases/{id}`: cabecera con el teléfono completo, que se audita como `PII_VIEW`; cotización y guía |
  | P0a | `GET /api/v1/advisor/sessions/{session_id}/transcript` · `GET`/`POST /api/v1/advisor/{leads\|cases}/{id}/timeline` · `POST /api/v1/advisor/{leads\|cases}/{id}/contact-attempts` |
  | P0b | `POST /api/v1/advisor/leads/{id}/close` (destinatario; emite la guía) · `POST /api/v1/advisor/leads/{id}/agency-handoffs` · `GET /api/v1/advisor/tracking/{guia}` |
  | P0b | `GET /api/v1/advisor/dashboard` · `GET`/`POST /api/v1/advisor/tasks` · `PATCH /api/v1/advisor/tasks/{id}` |
  | P0b | `GET /api/v1/advisor/notifications` · `POST /api/v1/advisor/notifications/read` · `POST /api/v1/advisor/rag/search` (sobre `RagService.retrieve`; cita por sección, sin página) |

  - **Errores:** `VERSION_DESACTUALIZADA` (409), `TRANSICION_NO_PERMITIDA`, `JUSTIFICACION_REQUERIDA` y
    `DATOS_INCOMPLETOS` (422), según las convenciones comunes.
  - **SLA:** `data/config/sla.json`. Lead: 15 min al primer contacto. Casos: Alta 30 min, Media 2 h y
    Baja 8 h. Meta del 90 %. Aviso al consumir el 80 % del plazo. El backend devuelve `sla_due_at` y
    `server_time`; el cronómetro lo pinta el front.
- **Por qué:**
  - El front del asesor ya está construido con mocks y necesita un backend real.
  - El lead del CRM y la gestión comercial de la spec 1.1 son la misma entidad: mantener las dos
    duplicaría estados y datos.
- **Compatibilidad:** aditivo (MINOR): campos opcionales, rutas, tablas y columnas nuevas. Los handoffs
  mantienen su `status` público. Hay dos **cambios de comportamiento** que no son de contrato y necesitan
  aprobación:
  - los textos del bot;
  - "hablar con asesor" después de cotizar abre un lead en lugar de un caso CRIT-002 (D-CRM5).
- **Ejemplo:**
  - `POST /api/v1/auth/login` → `{"access_token": "tlxs_…", "expires_in": 43200, "asesor": {"nombre": "Asesora Demo", "iniciales": "AD", "roles": ["ASESOR"], "disponibilidad": "DISPONIBLE"}}`.
  - Un `PATCH …/stage` con `expected_version` viejo → 409 `VERSION_DESACTUALIZADA`.
- **Decisiones requeridas:**
  - **D-CRM2: lista exacta de etapas del front.**
  - D-CRM3: motor del chat.
  - D-CRM4: destinatario al cierre.
  - D-CRM5: lead o caso al pedir asesor.
  - D-CRM6: prioridad.
  - D-CRM7: cliente sin teléfono.
  - D-CRM8: "recolección".
  - D-CRM9: envío anulado.
  - D-CRM1 (canal del seguimiento automático) solo bloquea P1.

  Las propuestas por defecto están en `REVISION_SOLICITUD_ASESOR.md` §4.
- **Estado:** PROPUESTA

### CR-008 — Endurecimiento tras la auditoría integral

- **Solicitante:** auditoría integral del 2026-11-04 ([`audits/INTEGRAL_AUDIT.md`](audits/INTEGRAL_AUDIT.md)).
- **Fecha:** 2026-11-04
- **Qué cambia:**
  - `ErrorCode.CUERPO_DEMASIADO_GRANDE` (413, `details.max_bytes`), documentado en toda operación con
    cuerpo salvo el lote B2B, que mantiene `LOTE_DEMASIADO_GRANDE` (DEC-AUD1).
  - Sin cambio de forma:
    - admisión acotada al LLM y un pool propio para la recuperación RAG (DEC-AUD2, DEC-AUD3);
    - bloqueo de fila por turno (DEC-AUD4);
    - texto de espera del asesor (DEC-AUD5).
- **Por qué:** hallazgos medidos:
  - SEC-04/05: un cuerpo de 25 MB se almacenaba entero;
  - REL-01: 10 de 24 peticiones RAG simultáneas daban 500;
  - CON-01: dos turnos simultáneos perdían un dato;
  - R1: el chat prometía una respuesta del asesor que no existe.
- **Compatibilidad:** aditivo (MINOR). Un cliente que no conozca el código nuevo recibe el sobre de error
  de siempre con HTTP 413. Sin migración.
- **Versión:** contrato **1.6.0**. CR-007 pasa a 1.7.0 (después a 1.8.0, por CR-009).
- **Estado:** IMPLEMENTADA

### CR-009 — RBAC mínimo: ADMIN, CLIENT, REVIEWER

- **Solicitante:** equipo (implementación prioritaria de RBAC), 2026-11-04.
- **Qué cambia:**
  - `Role` sustituye a los roles de personal 1.4.0;
  - 6 operaciones nuevas (`/auth/me` y `/internal/*`);
  - `StaffToken` como alternativa en `/handoffs*`, reindex y lectura de lotes;
  - migración 0005 (roles a REVIEWER, asignado del *handoff* y `shipment_reviews`).
- **Por qué:**
  - separar autenticación y autorización;
  - aislar clientes;
  - que REVIEWER no tenga acciones de ADMIN;
  - que CLIENT no apruebe mercancía peligrosa.

  Ver [`RBAC.md`](RBAC.md).
- **Compatibilidad:** aditivo en la forma. `X-Admin-Token` sigue siendo ADMIN. Cambios de
  comportamiento:
  - sin credencial → 401 (antes 403) en `/handoffs*` y reindex;
  - token de personal en `POST /shipment-batches` → 403 (antes 401).
- **Versión:** contrato **1.7.0**, migración **0005**.
- **Estado:** IMPLEMENTADA

### CR-010 — Remediación prioritaria: SEC-01, SEC-06, PII-01, BIZ-01, CAP-01, OPS-01 y LLM-01

- **Solicitante:** equipo (remediación prioritaria del backend), 2026-11-04.
- **Qué cambia:** ver CHANGELOG 2.0.0 y DECISIONS §10.
  - Historial y adjuntos ligados al dueño (`X-Client-Id`).
  - Estado de revisión de la mercancía peligrosa.
  - `SERVICIO_SATURADO`.
  - Cifrado de la PII en reposo.
  - *Rate limit* compartido.
  - Reintento acotado de Ollama.
  - Migraciones 0006 (revisión y *rate limit*) y 0007 (cifrado, reversible con la clave).
- **Compatibilidad:** **MAJOR**. `GET /chat/sessions/{id}/history` y `POST /attachments` exigen
  `X-Client-Id`, y los adjuntos además `session_id`. Lo demás es aditivo. Guía:
  [`FRONTEND_CAMBIOS_2_0_0.md`](FRONTEND_CAMBIOS_2_0_0.md).
- **Coordinación:**
  - CR-008 (1.6.0) ya está implementada.
  - CR-007 pasa a 2.1.0 / 0008.
- **Versión:** contrato **2.0.0**, migraciones **0006** y **0007**.
- **Estado:** IMPLEMENTADA en el código. El despliegue en `:8000` está pendiente de aviso al front y de
  la clave del entorno.

