# Usuarios y roles (RBAC): guía para el frontend

| | |
|---|---|
| Contrato | **1.7.0** (CR-009). Tipos en `contracts/types/api.d.ts` y OpenAPI en `contracts/openapi.yaml` |
| Backend (túnel) | `https://twin-shadows-assumed-progressive.trycloudflare.com` |
| CORS | Se acepta el origen `http://localhost:5173`. Si despliegan en otro origen, avísennos para añadirlo |
| Credenciales de demo | Van **aparte**, en `credenciales-frontend-demo.md`, por un canal privado. No están en este documento ni en el repositorio |

La URL del túnel **cambia** si se reinicia el túnel. Si deja de responder, pidan la nueva.

## 1. Los tres roles

| Rol | Quién es | Credencial | Panel sugerido |
|---|---|---|---|
| `ADMIN` | Administración global | Token personal `tlxs_…` | Usuarios internos, auditoría, bandeja completa, lotes de cualquier cliente y reindexación del RAG |
| `REVIEWER` | Asesor humano y revisor de casos escalados | Token personal `tlxs_…` | Bandeja de casos (la cola y los suyos) y revisión de mercancía peligrosa |
| `CLIENT` | Cliente empresarial (B2B) | API key `tlx_…` | Sus lotes de envíos: crear y consultar |

**Reglas que el backend aplica siempre (el front no tiene que reforzarlas):**
- **El rol sale de la credencial.** Enviar un rol en un *header* o en el *body* no sirve de nada.
- **Recursos ajenos:** un CLIENT nunca ve los lotes de otro cliente, aunque cambie el id en la URL;
  recibe **404**.
- **Casos de otro revisor:** un REVIEWER no ve ni puede cerrar el caso que tomó otro revisor;
  recibe **404**.
- **Mercancía peligrosa:** solo un REVIEWER puede tomar la decisión. Ni CLIENT ni ADMIN pueden.
- **Roles propios:** nadie puede cambiarse sus propios roles.

## 2. Cuentas de demostración

Hay una por rol, creadas en el backend de desarrollo:

| Rol | Cuenta | Credencial (prefijo público) | Vence |
|---|---|---|---|
| ADMIN | `admin.demo@translogix.co` (Admin Demo) | Token personal `tlxs_a80ab9a7b0e9_…` | 2027-01-03 |
| REVIEWER | `revisor.demo@translogix.co` (Revisor Demo) | Token personal `tlxs_e5ca11537685_…` | 2027-01-03 |
| CLIENT | Cliente Demo B2B S.A.S. (NIT 900999001-1) | API key `tlx_f076464b6416_…`, *scopes* `batches:create` y `batches:read` | Sin vencimiento (revocable) |

Los tokens completos están en el archivo privado. Si necesitan otra cuenta, por ejemplo un segundo
REVIEWER para probar que los casos de uno no los ve el otro, pídanla al backend.

**Datos de demo ya cargados:**
- **Lote `DEMO-RBAC-001`** del cliente demo (`lote_id` `b132fafd-3366-4cbd-bcfe-e280c753e618`). Tiene
  2 envíos y uno está declarado como mercancía peligrosa, pendiente de revisión: aparece en el panel
  del REVIEWER.
- **Bandeja:** hay casos escalados del chat en estado PENDING. Se pueden crear más escribiendo en el
  chat, por ejemplo: "Voy a demandar ante la Supertransporte".

**Compatibilidad:** el `X-Admin-Token` que ya usan en la bandeja sigue funcionando y cuenta como
ADMIN. Recomendamos pasar a los tokens personales, para que cada acción quede firmada por una
persona.

## 3. Cómo autenticar

No hay login con contraseña todavía; llegará con el CRM (CR-007). Por ahora:

1. Una pantalla de acceso en la que se pega el token (o una variable de entorno en desarrollo).
2. Todas las peticiones protegidas llevan `Authorization: Bearer <token>`.
3. `GET /api/v1/auth/me` dice qué rol tiene ese token; con eso se decide qué paneles mostrar.

```ts
const BASE = "https://twin-shadows-assumed-progressive.trycloudflare.com/api/v1";

async function api<T>(path: string, token: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, ...init.headers },
  });
  if (res.status === 401) throw new Error("SESION");   // token ausente, inválido o vencido → pantalla de acceso
  if (res.status === 403) throw new Error("PERMISO");  // token válido sin ese rol → "No tienes permiso"
  if (!res.ok) throw await res.json();                 // sobre de error estándar
  return res.json() as Promise<T>;
}

type Role = components["schemas"]["Role"];             // "ADMIN" | "CLIENT" | "REVIEWER"
const me = await api<components["schemas"]["WhoAmI"]>("/auth/me", token);
const can = (r: Role) => me.roles.includes(r);         // un usuario interno puede tener ADMIN y REVIEWER
```

Respuesta de `GET /api/v1/auth/me`:

```json
{
  "roles": ["REVIEWER"],
  "metodo": "STAFF_TOKEN",
  "staff_user_id": "8043876f-c144-406c-9a4e-db8f75d68682",
  "client_id": null,
  "scopes": []
}
```

- `metodo` puede ser `STAFF_TOKEN`, `API_KEY` o `ADMIN_TOKEN`.
- Un CLIENT trae `client_id` y `scopes`.

## 4. Qué puede hacer cada rol

| Panel | Endpoint | ADMIN | REVIEWER | CLIENT |
|---|---|:-:|:-:|:-:|
| Quién soy | `GET /auth/me` | ✔ | ✔ | ✔ |
| Bandeja de casos | `GET /handoffs?status=` | ✔ todos | ✔ cola PENDING + sus casos | ✘ |
| Detalle de un caso | `GET /handoffs/{id}` | ✔ | ✔ si está PENDING o es suyo | ✘ |
| Tomar o cerrar un caso | `PATCH /handoffs/{id}` | ✔ | ✔ toma de la cola, cierra solo los suyos | ✘ |
| Mercancía peligrosa (lista) | `GET /internal/dangerous-goods?estado=` | ✔ | ✔ | ✘ |
| Mercancía peligrosa (decidir) | `POST /internal/shipments/{id}/dangerous-goods-review` | ✘ | ✔ | ✘ |
| Usuarios internos (crear) | `POST /internal/staff-users` | ✔ | ✘ | ✘ |
| Usuarios internos (roles) | `PATCH /internal/staff-users/{id}/roles` | ✔ salvo los propios | ✘ | ✘ |
| Auditoría | `GET /internal/audit-events?limit=&action=` | ✔ | ✘ | ✘ |
| Crear lote B2B | `POST /shipment-batches` | ✘ | ✘ | ✔ |
| Ver lote e ítems | `GET /shipment-batches/{id}`, `/{id}/items` | ✔ cualquiera | ✘ | ✔ solo los suyos |
| Reindexar el RAG | `POST /admin/rag/reindex` | ✔ | ✘ | ✘ |

**Siguen públicos, sin login:** chat, rastreo, cotización (`/cotizar` y `/cotizaciones`), PQRS,
métricas, `/health` y `/ready`. Los flujos actuales de la demo no cambian.

**Desde 2.0.0** (ver [`FRONTEND_CAMBIOS_2_0_0.md`](FRONTEND_CAMBIOS_2_0_0.md)), el historial del chat y
los adjuntos exigen `X-Client-Id`; los adjuntos, además, `session_id`. ADMIN lee cualquier historial y
REVIEWER, el de los casos de su bandeja.

## 5. Detalle de los endpoints nuevos o cambiados

Todas las rutas van con el prefijo `/api/v1`. Los ejemplos completos están en `contracts/examples/`.

### Bandeja de casos (cambia: ahora también con token personal)

- **Tomar un caso:** `PATCH /handoffs/{id}` con `{"status": "ASSIGNED"}`. Queda asignado a quien lo
  toma.
- **Cerrar un caso:** `{"status": "CLOSED"}`. Un REVIEWER solo cierra los suyos; si cierra uno de la
  cola, lo toma y lo cierra.
- **Caso ajeno:** si es de otro revisor, la respuesta es `404`. Muéstrenlo como "ya no está
  disponible".
- **ADMIN:** con un token personal o con `X-Admin-Token`, lo ve y lo hace todo.

### Mercancía peligrosa

`GET /internal/dangerous-goods?estado=PENDIENTE|REVISADO&limit=50` (ADMIN y REVIEWER):

```json
{
  "items": [
    {
      "shipment_id": "…",
      "numero_guia": "TLX-200014-CO",
      "codigo_trayecto": "TLX-BOG-MED-001",
      "created_at": "2026-11-04T08:30:00-05:00",
      "revision": null
    }
  ]
}
```

- `revision: null` significa que está pendiente.
- Nunca trae datos del destinatario.

`POST /internal/shipments/{shipment_id}/dangerous-goods-review` (solo REVIEWER) → `201`:

```json
{ "decision": "APROBADA", "observaciones": "MSDS vigente, embalaje UN homologado y rotulado Clase 8 verificados." }
```

| Campo | Valores |
|---|---|
| `decision` | `APROBADA` o `RECHAZADA` |
| `observaciones` | De 5 a 2.000 caracteres |

Hay una sola decisión por envío; una segunda responde `422`. La decisión **queda registrada, pero
todavía no bloquea el envío**: esa regla está pendiente de negocio.

### Usuarios internos (ADMIN)

`POST /internal/staff-users` → `201`:

```json
{ "email": "revisora@translogix.co", "nombre": "Revisora de turno", "roles": ["REVIEWER"] }
```

`PATCH /internal/staff-users/{id}/roles` → `200`:

```json
{ "roles": ["ADMIN", "REVIEWER"] }
```

- Solo admite los roles `ADMIN` y `REVIEWER`; `CLIENT` responde `422`.
- Sobre el propio usuario responde `403` con `details.motivo = "propio_rol"`. Deshabiliten ese control
  en la UI cuando `id == me.staff_user_id`.
- El cambio aplica desde la siguiente petición de esa persona.
- **El token de un usuario nuevo no sale por la API**: lo emite el backend. No hay listado de
  usuarios todavía.

### Auditoría (ADMIN)

`GET /internal/audit-events?limit=50&action=access.denied` devuelve los eventos más recientes primero:
`action`, `http_status`, `client_id`, `staff_user_id`, `resource_type`, `resource_id` y
`created_at`. No incluye credenciales ni PII.

| Tipo | Acciones |
|---|---|
| Autenticación y acceso | `auth.failed` (401), `access.denied` (403) |
| Usuarios internos | `staff.create`, `staff.roles.update` |
| Bandeja | `handoff.read`, `handoff.update` |
| Mercancía peligrosa | `dangerous_goods.read`, `dangerous_goods.review` |
| Lotes | `batch.create`, `batch.read`, `batch.items.read` |
| Operación | `rag.reindex`, `audit.read` |

### Lotes B2B

- Sin cambios para el CLIENT.
- Novedad: un ADMIN puede leer cualquier lote por su id.
- **No existe un endpoint para listar los lotes**: se consultan por `lote_id`, el que devuelve la
  creación.

## 6. Errores

Todos los errores usan el mismo sobre:

```json
{
  "error": {
    "code": "NO_AUTORIZADO",
    "message": "…",
    "details": { "roles_requeridos": ["ADMIN"] },
    "request_id": "…"
  }
}
```

| HTTP | `code` | Cuándo | Qué hacer en la UI |
|---|---|---|---|
| 401 | `NO_AUTENTICADO` | Sin token, token inválido, vencido o revocado | Volver a la pantalla de acceso |
| 403 | `NO_AUTORIZADO` | Token válido sin el rol. `details`: `roles_requeridos`, `scope_requerido` o `motivo: propio_rol` | "No tienes permiso"; ocultar la acción |
| 404 | `NO_ENCONTRADO` | No existe **o pertenece a otro**; el backend no distingue los dos casos | "No existe o no tienes acceso" |
| 422 | `VALIDACION` | Datos inválidos, segunda decisión, envío que no es peligroso, rol CLIENT para un usuario interno | Mostrar `message` |

## 7. Qué cambió respecto a 1.6.0

| Cambio | Antes | Ahora |
|---|---|---|
| Roles internos | `ASESOR`, `SUPERVISOR`, `OPERADOR_LOGISTICO`, `REVISOR_ESPECIALIZADO` (no los exponía ningún endpoint) | `ADMIN` y `REVIEWER`. Si el CRM los usaba en su diseño, hay que cambiarlos |
| `/handoffs` y `/admin/rag/reindex` sin token o con un `X-Admin-Token` incorrecto | 403 | **401** |
| Token de personal en `POST /shipment-batches` | 401 | **403** |
| URL del túnel | — | `https://twin-shadows-assumed-progressive.trycloudflare.com` (la anterior ya no responde) |
| Endpoints nuevos | — | `/auth/me` y `/internal/*` |

## 8. Limitaciones actuales

| Limitación | Detalle |
|---|---|
| Sesión | No hay login, logout ni contraseña. El backend emite los tokens (60 días) y los revoca a petición |
| Listados | No se pueden listar lotes ni usuarios internos |
| Asignaciones | No existe la gestión de PQRS asignadas a un revisor |
| Consultas de ADMIN | No hay consulta global de cotizaciones ni de PQRS |
| Respuesta al cliente | El asesor todavía no puede responderle por el chat; llega con CR-007 |
| Endpoints públicos | En 1.7.0, el historial del chat y los adjuntos siguen públicos. **Se cierran en 2.0.0** |

## 9. Lo que necesitamos de ustedes

1. **El origen donde desplegarán la PWA**, para habilitar CORS. Hoy solo está `http://localhost:5173`.
2. **Si la bandeja pasará de `X-Admin-Token` a tokens personales de REVIEWER.** Es la recomendación.
3. **Si necesitan más cuentas de prueba:** un segundo REVIEWER u otro CLIENT para mostrar el
   aislamiento.
