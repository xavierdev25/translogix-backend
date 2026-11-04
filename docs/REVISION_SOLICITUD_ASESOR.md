# Revisión de la solicitud del módulo Asesor (CRM)

Respuesta del backend a `solicitud-backend-asesor.md` y `asesor-backend-faltantes.md` (rama `kenith-crm`
del front). Esos documentos analizan `e96a727` (contrato 1.3.0). Desde entonces el backend avanzó:

| Versión | Entrega | Migración |
|---|---|---|
| **1.4.0** | Identidades internas (CR-002) | 0003 |
| **1.5.0** | Cotización por perfiles tarifarios (CR-003) | 0004 |

Esta revisión dice qué de la solicitud sigue siendo correcto, qué choca con lo ya construido y cómo
integrarlo. **No se implementó el CRM:** primero hay que acordar las decisiones de la §4.

**Registrado como [CR-007](contract-change-requests.md) (PROPUESTA), que sustituye a CR-004.**

## 1. Lo que la solicitud dice y sigue siendo cierto (verificado en el código)

| Afirmación | Estado hoy |
|---|---|
| No hay nada propio del CRM: leads, contactos, notas, tareas, SLA ni notificaciones | Cierto |
| El bot no pide teléfono ni correo | Cierto |
| El bot promete "Te responderá en este mismo chat" (`orchestrator.py:685`) | Cierto. Ya era el riesgo R1 de `BACKEND_CURRENT_STATE.md` |
| "Hablar con un asesor" siempre es CRIT-002 (ALTA, cola SUPERVISOR) | Cierto (`engine.py:149`) |
| Los handoffs tienen 3 estados sin retroceso | Cierto |
| `RagService.retrieve` existe sin endpoint, y las citas no tienen página | Cierto |
| La guía se puede emitir con `next_guia()` (`shipment_guide_seq`, 200000–299999) | Cierto |
| `shipments.client_id` es `NOT NULL` (FK a `enterprise_clients`) | Cierto |
| El tracking público no muestra guías internas (DEC-SHP8) | Cierto |
| **CORS no permite el header `Authorization`** | **Era cierto y ya se corrigió:** `Authorization` e `Idempotency-Key` permitidos, `X-Request-ID` expuesto, 3 pruebas en `tests/test_http_cors.py`. `PUT` y `DELETE` se añaden cuando exista un endpoint que los use |

Los números de línea citados corresponden a `e96a727`. En `main.py` ya se movieron.

## 2. Lo que choca con lo construido después de `e96a727`

| # | La solicitud propone | Lo que existe hoy | Recomendación |
|---|---|---|---|
| C1 | Contrato **1.4.0** y migración **`0003_crm_asesor`** (`down_revision="0002"`) | 1.4.0 y 1.5.0 ya están publicados; 0003 y 0004 ya existen | CRM = contrato **1.6.0**, migración **0005** (`down_revision="0004"`) |
| C2 | Tabla `advisors` nueva, login con contraseña y **JWT HS256** (`pyjwt`, `pwdlib`, `AUTH_JWT_SECRET`) | **CR-002 ya creó `staff_users`** con roles `ASESOR` y `SUPERVISOR`, más token personal `tlxs_…` guardado como hash, revocable, con vencimiento, la dependencia `staff_authenticated(*roles)` y auditoría | **No duplicar identidades.** Extender CR-002: `staff_users.password_hash` y `availability`, y `POST /api/v1/auth/login` (correo + contraseña) que devuelve un **token `tlxs_` de corta vida** (p. ej. 12 h, el turno). El front lo usa igual que un JWT (`Authorization: Bearer`). Ventajas: una sola vía de autenticación, logout = revocar, sin secreto JWT que custodiar y sin `pyjwt`. Para el hash de la contraseña basta `hashlib.scrypt` de la librería estándar; `argon2` (`pwdlib`) también sirve si se prefiere |
| C3 | Entidad `leads`, con etapas propias del tablero de ventas | La especificación 1.1 (CR-004, PROPUESTA) define la **gestión comercial** con 7 estados (`PENDIENTE_ASESOR` … `CANCELADO`) | **Una sola entidad.** El lead del CRM *es* la gestión comercial de la 1.1. Se usa el nombre `leads` del front con una máquina de estados acordada (D-CRM2) |
| C4 | El lead apunta a `quotes` (`provisional-v1`) | CR-003 introdujo `tariff_quotations` (perfiles TEST_ONLY, `excedente-v1`). La 1.1 pide que la cotización con perfil abra la gestión comercial. **El chat sigue cotizando con `provisional-v1`** | Hoy el lead debe poder referenciar **cualquiera de las dos**: dos FK nullable con `CHECK` de que haya exactamente una. **Pasar el chat al motor por perfiles es una decisión aparte** (D-CRM3): la matriz de prueba solo cubre 5 destinos, así que el chat dejaría de cotizar Medellín y la mayoría de rutas |
| C5 | Un 409 nuevo `CONFLICTO` para versión desactualizada | CR-004 y CR-005 (PROPUESTA) usan `ESTADO_DESACTUALIZADO` con `expected_status` | **Un solo mecanismo y un solo código:** `expected_version` (entero) en toda entidad con etapas, con un único código 409 (`VERSION_DESACTUALIZADA`). Se ajustan CR-004 y CR-005 |
| C6 | Rutas `/advisor/*` y `/auth/*` | CR-004 y CR-005 proponían `/api/v1/internal/*` (no aceptadas) | Adoptar la convención del front: `/api/v1/advisor/*` y `/api/v1/auth/*`. Se actualizan CR-004 y CR-005 |
| C7 | Deshacer un cambio de etapa en 30 s, incluso después de emitir la guía | Un envío creado no se borra (historial inmutable, spec 1.1 §7) | Deshacer **no** aplica al paso que emite la guía: el front pide confirmación antes de cerrar la venta. Anular un envío ya emitido es un cambio logístico a `CANCELADO` con justificación (CR-005) |
| C8 | Pedir un asesor después de cotizar crea un **lead** (no un caso) | Las reglas JSON mandan cualquier pedido explícito de asesor a CRIT-002 | Es razonable, pero **cambia la política de escalamiento**: debe aprobarlo negocio (D-CRM5). Las palabras clave legales y el sentimiento muy negativo siguen siendo CRIT-002 |

## 3. Lo que la solicitud aporta y conviene adoptar

- **Captura de contacto en el bot:** consentimiento (Ley 1581), teléfono y correo, evaluada **antes** de
  `_still_waiting`. Responde la pregunta abierta de CR-004 "¿de dónde salen los datos de contacto?". Va
  acompañada de `interactive_reply_id` en el Web Chat, que es el riesgo R2 del backend.
- **Cifrado de PII:** Fernet con `PII_ENCRYPTION_KEY`, HMAC con `PII_HASH_SALT` para buscar, y
  `phone_last4` para mostrar. Es una propuesta concreta para **D1**, que bloquea producción.
  - Debe aplicarse también a `shipment_recipients`, que hoy guarda los datos en claro (DEC-SHP9).
  - Hay que añadir rotación (`MultiFernet` con `key_id`) y la auditoría `PII_VIEW`.
- **Transcripción redactada en origen** en los turnos de captura, más **SLA** con `sla_due_at` y
  `server_time`.
- **Notificaciones por polling, y SSE después.** Es coherente con la arquitectura (sin WebSocket y sin
  workers nuevos).
- **Texto del bot "Un asesor te llamará al ···4567":** corrige R1. Va enmascarado, así que pasa la
  validación DEC-S1.
- **Envío al cerrar la venta:** `shipments.client_id` nullable más `lead_id`, con
  `CHECK (client_id IS NOT NULL OR lead_id IS NOT NULL)`. Así se reutilizan la secuencia de guías, el
  destinatario separado y el snapshot de precio del P0.
- **`GET /api/v1/advisor/tracking/{guia}`** autenticado para guías internas. Es compatible con DEC-SHP8,
  porque el tracking público sigue sin mostrarlas.

## 4. Decisiones necesarias antes de implementar

Las 6 de la solicitud, más las que exige reconciliarla con la spec 1.1:

| Id | Decisión | Propuesta |
|---|---|---|
| D-CRM1 | Canal del seguimiento automático (embudo C) | Correo. Mientras tanto, simulado |
| D-CRM2 | **Lista de etapas del tablero de ventas** y su equivalencia con la 1.1 | `NUEVA_COTIZACION`=`PENDIENTE_ASESOR` · `EN_CONTACTO`=`EN_SEGUIMIENTO` · `DATOS_PENDIENTES` · `VENTA_CERRADA`=`LISTO_PARA_AGENCIA` (crea el envío) · `ENTREGADO_A_AGENCIA` · `DESCARTADO`=`CANCELADO` (con motivo). **El front debe confirmar sus etapas exactas** |
| D-CRM3 | ¿Con qué motor cotiza el chat cuyo resultado abre el lead? | Seguir con `provisional-v1` hasta que exista una matriz que cubra todas las rutas. El lead acepta ambas cotizaciones (C4) |
| D-CRM4 | Destinatario al cerrar la venta | Lo captura el asesor en un formulario de cierre; el bot ya pide teléfono y correo, y pedir dirección por chat alarga el flujo |
| D-CRM5 | "Hablar con asesor" después de cotizar → lead en lugar de caso | Aceptar, sin tocar CRIT-002 para lenguaje legal o sentimiento muy negativo |
| D-CRM6 | Prioridad Alta/Media/Baja | La de la solicitud (§10.2 de `asesor-backend-faltantes.md`) |
| D-CRM7 | Cliente que no deja teléfono | Cotización sin lead. El caso urgente se muestra en B como "sin contacto" y lo resuelve Operaciones con la guía |
| D-CRM8 | "Recolección" | Quitarla de la UI. Si negocio la define, entra como componente del perfil tarifario (DEC-TAR4 deja abiertos los componentes adicionales) |
| D-CRM9 | Estado de un envío anulado | `CANCELADO` logístico con justificación (CR-005), nunca borrado |

## 5. Plan recomendado

**Un solo CR** ("CRM del asesor", contrato 1.6.0, migración 0005) que **sustituye a CR-004**. CR-005
(logística) y CR-006 (mercancía peligrosa) siguen aparte.

Se divide en entregas para que el front conecte pronto:

| Entrega | Contenido | Desbloquea en el front |
|---|---|---|
| **P0a** | Login (`POST /api/v1/auth/login`, extensión de CR-002), `GET /api/v1/advisor/me` y disponibilidad; captura de contacto en el bot; `contacts` cifrados; `leads` y enlace de casos; tableros A y B; cambio de etapa con `expected_version`; detalle, transcripción y timeline | Reemplazar los mocks de MSW de me, embudos, detalle y notas |
| **P0b** | Cierre de venta con emisión de guía (C7, D-CRM4); `GET /api/v1/advisor/tracking/{guia}`; tareas; dashboard y SLA; notificaciones por polling; `POST /api/v1/advisor/rag/search` | Dashboard, tareas, herramientas y notificaciones |
| **P1** | Embudo C con scheduler; SSE; SLA en horario laboral; búsqueda global; refresh | Como en la solicitud |

**Para el front (`crm-api.ts`):**
- La autenticación queda igual que un JWT: `Authorization: Bearer <token>`.
- Un 401 obliga a volver a hacer login (token vencido o revocado).
- Las rutas serán las de la §6 de la solicitud, con el prefijo `/api/v1`.
- `CONFLICTO` pasa a llamarse `VERSION_DESACTUALIZADA` (409).
