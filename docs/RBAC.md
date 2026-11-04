# RBAC — TransLogix AI (contrato 1.7.0, CR-009)

Autorización mínima basada en roles. Hay exactamente tres roles, en el enum `Role`
(`backend/app/contracts/staff.py`):

| Rol | Quién | Credencial |
|---|---|---|
| `ADMIN` | Administración global | Token personal `tlxs_…` con rol ADMIN, o el `X-Admin-Token` heredado |
| `CLIENT` | Cliente B2B externo | API key `tlx_…` (con *scopes*) |
| `REVIEWER` | Asesor humano y revisor de casos escalados y de mercancía peligrosa | Token personal `tlxs_…` con rol REVIEWER |

**Principios:**
- **Autenticación y autorización separadas:**
  - `app/auth/principal.py` convierte una credencial en un `Principal`;
  - `app/auth/dependencies.py` (`require(*roles, scope=...)`) comprueba el rol.
- **Denegación por defecto:**
  - `require()` sin roles es un error al arrancar;
  - la prueba `test_every_route_is_protected_or_explicitly_public` falla si aparece una ruta que no
    está protegida ni en la lista pública.
- **El rol sale siempre de la credencial.** Nunca se lee un rol de *headers* ni del *body*; hay una
  prueba que lo verifica.
- **Propiedad en el repositorio:**
  - un CLIENT solo ve sus lotes (`BatchRepository.get(batch_id, client_id)`);
  - un REVIEWER solo ve la cola PENDING y sus propios casos (`HandoffRepository.list_by_status(...,
    reviewer=...)`, `HandoffService.get_visible`).
- **401 frente a 403:**
  - sin credencial, o con una inválida → **401** `NO_AUTENTICADO`;
  - con credencial válida pero sin el rol → **403** `NO_AUTORIZADO`.

  Ambos se auditan.

## 1. Endpoints protegidos por rol

| Operación | ADMIN | REVIEWER | CLIENT | Regla de propiedad |
|---|:-:|:-:|:-:|---|
| `GET /api/v1/auth/me` | ✔ | ✔ | ✔ | Devuelve la identidad de la propia credencial |
| `GET /api/v1/handoffs` | ✔ (todos) | ✔ | ✘ | REVIEWER: la cola PENDING y sus casos asignados |
| `GET /api/v1/handoffs/{id}` | ✔ | ✔ | ✘ | REVIEWER: otro caso responde 404 (sin oráculo) |
| `PATCH /api/v1/handoffs/{id}` | ✔ | ✔ | ✘ | Al salir de PENDING se asigna a quien actúa. REVIEWER: solo cierra los suyos |
| `POST /api/v1/admin/rag/reindex` | ✔ | ✘ | ✘ | — |
| `POST /api/v1/shipment-batches` | ✘ | ✘ | ✔ `batches:create` | Lote del cliente de la API key |
| `GET /api/v1/shipment-batches/{id}` | ✔ (global) | ✘ | ✔ `batches:read` | CLIENT: un lote ajeno responde 404 |
| `GET /api/v1/shipment-batches/{id}/items` | ✔ (global) | ✘ | ✔ `batches:read` | Ídem |
| `POST /api/v1/internal/staff-users` | ✔ | ✘ | ✘ | Solo los roles ADMIN y REVIEWER; CLIENT → 422 |
| `PATCH /api/v1/internal/staff-users/{id}/roles` | ✔ | ✘ | ✘ | Nunca los propios: 403 `motivo: propio_rol` |
| `GET /api/v1/internal/audit-events` | ✔ | ✘ | ✘ | — |
| `GET /api/v1/internal/dangerous-goods` | ✔ | ✔ | ✘ | Solo envíos con `mercancia_peligrosa`, **sin datos del destinatario** |
| `POST /api/v1/internal/shipments/{id}/dangerous-goods-review` | ✘ | ✔ | ✘ | Una decisión por envío. Solo REVIEWER (separación de funciones) |

**Compatibilidad del `X-Admin-Token`:** cuenta como ADMIN en todas las operaciones ADMIN, salvo
`reviewDangerousGoods`, que exige una persona identificable (devuelve 401).

**Acciones auditadas en `api_audit_events`:**
- `auth.failed` (401) y `access.denied` (403);
- `staff.create` y `staff.roles.update`;
- `handoff.read` y `handoff.update`;
- `rag.reindex` y `audit.read`;
- `dangerous_goods.read` y `dangerous_goods.review`;
- `batch.*` (ya existían).

Se registra el actor (`client_id` / `staff_user_id`), el recurso y el estado HTTP, nunca la
credencial. Se usa `api_audit_events` (la auditoría de la API) y no `audit_events`, que es la
auditoría por turno de conversación y tiene otro esquema.

## 2. Archivos y migración

| Área | Archivos |
|---|---|
| Roles y contrato | `app/contracts/staff.py` (`Role`, `STAFF_ROLES`), `app/contracts/auth.py` (modelos nuevos), `app/contracts/api_spec.py` (6 operaciones nuevas y seguridad de las existentes), `app/contracts/examples.py`, `app/contracts/common.py` (1.7.0) |
| Autenticación y autorización | `app/auth/principal.py`, `app/auth/dependencies.py`, `app/auth/routes.py` |
| Uso | `app/main.py` (handoffs y reindex), `app/batches/api/routes.py`, `app/batches/application/service.py`, `app/batches/infrastructure/repositories.py` (`get_any`, solo ADMIN) |
| Casos escalados | `app/modules/escalation/handoff.py` (`get_visible`, asignación), `app/db/repositories.py`, `app/db/models.py` (`HandoffRow.assigned_staff_user_id`) |
| Mercancía peligrosa | `app/shipments/infrastructure/tables.py` (`ShipmentReviewRow`), `app/shipments/infrastructure/repositories.py` (`ShipmentReviewRepository`) |
| Personal | `app/staff/*` (`StaffRole` → `Role`; un usuario interno no puede tener CLIENT) |
| Auditoría | `app/audit/domain/models.py` (acciones nuevas), `app/audit/infrastructure/repository.py` |
| Migración | `migrations/versions/0005_rbac.py` |
| Contrato generado | `contracts/openapi.yaml`, `contracts/schemas/*`, `contracts/examples/*` (8 nuevos), `contracts/types/api.d.ts` |

**Migración `0005`:**
- **Upgrade:**
  - los roles 1.4.0 (ASESOR, SUPERVISOR, OPERADOR_LOGISTICO, REVISOR_ESPECIALIZADO) pasan a
    `REVIEWER`; nadie obtiene ADMIN por la migración;
  - columna nullable `handoffs.assigned_staff_user_id`;
  - tabla `shipment_reviews`.
- **Downgrade:** REVIEWER → ASESOR y ADMIN → SUPERVISOR. Es una pérdida conocida, porque el upgrade
  fusiona cuatro roles en uno.
- **Prueba:** se ejecutó upgrade → downgrade → upgrade sobre `translogix_test` con 3 usuarios en los
  roles antiguos, y se verificaron las columnas, la tabla y los roles en cada paso. Después,
  `alembic check` no detectó cambios pendientes.
- **Pendiente:** aún no se aplicó a la base de desarrollo (`translogix`, en 0004) ni al servidor
  `:8000`. Ver §6.

## 3. Pruebas

**`tests/auth/test_rbac.py`:** 39 pruebas, sobre `translogix_test` y con credenciales creadas en la
propia prueba.

| Qué se prueba | Resultado esperado |
|---|---|
| Las 13 operaciones protegidas sin credencial | 401 |
| Credenciales inválidas: *bearer* basura, `tlxs_`/`tlx_` falsos, `X-Admin-Token` incorrecto | 401 |
| `/auth/me` con cada credencial | El rol correcto |
| Rol enviado en *headers* o *body* | Se ignora |
| REVIEWER contra reindex, alta de usuarios, cambio de roles, auditoría y lectura de lotes | 403 |
| CLIENT contra handoffs, reindex, alta de usuarios, auditoría, lista y decisión de mercancía peligrosa | 403 |
| Un ADMIN cambia sus propios roles | 403 |
| Promoción de otro usuario | Se aplica en la siguiente petición |
| Usuario interno con rol CLIENT | 422 |
| Lote de A consultado por B (detalle e ítems) | 404 |
| El mismo lote consultado por ADMIN (token personal y `X-Admin-Token`) | 200 |
| El mismo lote consultado por un REVIEWER | 403 |
| Un ADMIN crea un lote | 403 |
| Decisión sobre mercancía peligrosa por CLIENT o ADMIN | 403 |
| Decisión con `X-Admin-Token` | 401 |
| Decisión por REVIEWER | 201 |
| Segunda decisión sobre el mismo envío | 422 |
| Decisión sobre un envío no peligroso | 422 |
| Decisión sobre un envío inexistente | 404 |
| La lista de mercancía peligrosa | No expone datos del destinatario |
| REVIEWER2 ve y cierra el caso asignado a REVIEWER1 | No: 404 en ambos |
| ADMIN y `X-Admin-Token` ven todos los casos | Sí |
| Auditoría de la denegación (con el actor) y del alta de usuario | Registradas, sin restos de credenciales |
| Guarda de denegación por defecto | 28 rutas: 13 protegidas y 15 públicas listadas |

**Pruebas existentes ajustadas a la semántica nueva:**

| Prueba | Antes | Ahora |
|---|---|---|
| Token de personal en `POST /shipment-batches` | 401 | 403 (credencial válida sin el rol) |
| Auditoría de 403 | `auth.failed` | `access.denied` |
| Roles de las pruebas de personal | Roles 1.4.0 | ADMIN y REVIEWER |

## 4. Ejemplos de autenticación

```bash
# Personal interno (ADMIN o REVIEWER): alta por CLI y token personal (se muestra una sola vez)
uv run python -m app.staff.cli create-user --email revisora@translogix.co --nombre "Revisora" --role REVIEWER
uv run python -m app.staff.cli issue-token --user-id <id> --expires-in-days 30
curl -s localhost:8000/api/v1/auth/me -H "Authorization: Bearer $REVIEWER_TOKEN"
curl -s localhost:8000/api/v1/handoffs -H "Authorization: Bearer $REVIEWER_TOKEN"
curl -s -X POST localhost:8000/api/v1/internal/shipments/<id>/dangerous-goods-review \
  -H "Authorization: Bearer $REVIEWER_TOKEN" -H 'Content-Type: application/json' \
  -d '{"decision":"APROBADA","observaciones":"MSDS y embalaje UN verificados."}'

# ADMIN con token personal (o con el X-Admin-Token heredado)
curl -s localhost:8000/api/v1/internal/audit-events -H "Authorization: Bearer $ADMIN_STAFF_TOKEN"
curl -s localhost:8000/api/v1/handoffs -H "X-Admin-Token: $ADMIN_TOKEN"

# CLIENT: API key del cliente B2B
uv run python -m app.clients.cli create-client --nit 900123456-7 --razon-social "Cliente"
uv run python -m app.clients.cli create-key --client-id <id> --scope batches:create --scope batches:read
curl -s localhost:8000/api/v1/shipment-batches/<lote_id> -H "Authorization: Bearer $CLIENT_API_KEY"
```

Las variables (`$REVIEWER_TOKEN`, etc.) son los valores que devuelve la CLI. Ninguno está en el
repositorio.

## 5. Sin protección o pendientes (explícito)

**Públicos por diseño** (canales de cliente y salud), sin cambios en 1.7.0:
- `GET /tracking/{guia}`, `POST /cotizar`, `POST /cotizaciones` y `POST /pqrs/crear-ticket`;
- `POST /chat/messages`;
- WhatsApp (`GET`/`POST /webhook`, protegido por la firma HMAC de Meta; `POST /simulate`);
- PBX (`/turns` y `/simulate`);
- `GET /metrics/channels` (agregados, sin PII; el front lo usa sin token);
- `POST /auth/login` (CR-007: email y contraseña de personal; devuelve un token de 1 día).
  `POST /auth/logout` sí está protegida (revoca el token con que se llama);
- `/health` y `/ready`.

**Riesgos abiertos que este cambio no resuelve:**

| Riesgo | Por qué sigue abierto |
|---|---|
| `GET /chat/sessions/{id}/history` | **Cerrado en 2.0.0 (CR-010):** dueño con `X-Client-Id`, ADMIN o REVIEWER con un caso de esa sesión |
| `POST /attachments` | **Cerrado en 2.0.0 (CR-010):** `X-Client-Id` y `session_id` del mismo usuario, más límite por usuario |
| `POST /channels/*/simulate` | Deberían desactivarse o protegerse en producción |

**Partes de la matriz no implementadas** (no existe el endpoint o el modelo):

| Rol | Pendiente |
|---|---|
| ADMIN | Consulta global de cotizaciones y PQRS: no hay endpoints de lectura |
| ADMIN | Gestión de tarifas y configuración: solo por CLI (`app.tariffs.cli`), no por HTTP |
| ADMIN | Emisión y revocación de tokens personales y API keys: solo por CLI |
| REVIEWER | Gestión de PQRS asignadas: no existe un modelo de asignación de PQRS |
| CLIENT | No hay lectura de envíos sueltos ni de cotizaciones propias: las cotizaciones y las PQRS públicas no guardan cliente |

**Mercancía peligrosa:** la decisión se registra, pero **todavía no bloquea el envío**. Esa regla de
negocio es la decisión Q1 / CR-006.

## 6. Compatibilidad con el frontend

- **Flujos de demo sin cambios:**
  - chat, rastreo, cotización, PQRS, adjuntos y métricas siguen públicos;
  - la bandeja de asesor sigue funcionando con `X-Admin-Token`.
- **Cambio de comportamiento:** sin credencial o con un `X-Admin-Token` incorrecto, `/handoffs` y
  `/admin/rag/reindex` responden ahora **401 `NO_AUTENTICADO`** (antes 403). Con un token válido
  pero sin el rol, responden 403.
- **Nuevo y opcional para el front:**
  - `GET /api/v1/auth/me`, para conocer el rol;
  - tokens personales de REVIEWER en la bandeja, recomendado para que cada acción tenga autor;
  - las rutas `/internal/*`.
- **Roles:** los de 1.4.0 (ASESOR, SUPERVISOR…) ya no existen. Ningún endpoint los exponía, pero
  CR-007 y `RESPUESTA_FRONT_CRM.md` los mencionan; CR-007 debe usar ADMIN y REVIEWER.
- **Para desplegar en `:8000`:**
  1. ejecutar `alembic upgrade head` en la base de desarrollo (0004 → 0005);
  2. reiniciar el servidor.

  El código 1.7.0 no funciona sobre una base en 0004, porque lee `handoffs.assigned_staff_user_id`.
