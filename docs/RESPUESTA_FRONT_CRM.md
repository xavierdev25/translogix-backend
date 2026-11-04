# Respuesta del backend a la solicitud del módulo Asesor (CRM)

Para el front (rama `kenith-crm`). Detalle completo en [`REVISION_SOLICITUD_ASESOR.md`](REVISION_SOLICITUD_ASESOR.md)
y en el cambio de contrato [CR-007](contract-change-requests.md).

## Estado

- Revisamos `solicitud-backend-asesor.md` y `asesor-backend-faltantes.md`. El análisis es correcto, pero
  se hizo sobre `e96a727` (contrato 1.3.0) y el backend ya va en **1.5.0**:

  | Versión | Qué trajo |
  |---|---|
  | 1.4.0 | Usuarios internos con roles `ASESOR` y `SUPERVISOR` |
  | 1.5.0 | Cotización por perfiles tarifarios con IVA |

- La solicitud quedó registrada como **CR-007 (PROPUESTA)**: contrato **2.1.0** y migración **0008**.
  La 1.7.0 trajo el RBAC (CR-009): los roles internos son ahora `ADMIN` y `REVIEWER`, no ASESOR y
  SUPERVISOR. Ver [`RBAC.md`](RBAC.md).
  La 1.6.0 la tomó CR-008 (endurecimiento de la auditoría): añade el error 413
  `CUERPO_DEMASIADO_GRANDE` y cambia el texto de espera del asesor.
- Ya corregimos algo que señalaron: **CORS acepta `Authorization`** y expone `X-Request-ID`.

## Qué cambia respecto a lo que propusieron

1. **Login sin JWT.** `POST /api/v1/auth/login` con `{email, password}` devuelve un token de sesión de
   12 h, que se envía como `Authorization: Bearer <token>`. Para `crm-api.ts` funciona igual que un JWT:
   - un **401** significa volver a hacer login;
   - `POST /api/v1/auth/logout` revoca el token.

   No se crea una tabla `advisors`: se reutilizan los usuarios internos de 1.4.0.
2. **Rutas:** las de su §6 con prefijo `/api/v1`: `/api/v1/advisor/*` y `/api/v1/auth/*`.
3. **Concurrencia:** todo cambio de etapa envía `expected_version`, y una versión vieja responde **409
   `VERSION_DESACTUALIZADA`** (no `CONFLICTO`).
4. **Deshacer** no está disponible después de `VENTA_CERRADA`, porque ahí se emite la guía. La UI debe
   pedir confirmación antes de cerrar la venta. Un envío emitido se anula desde logística, con
   justificación.
5. **La cotización del lead** puede venir del chat (`provisional-v1`) o del motor por perfiles
   (`/api/v1/cotizaciones`). La tarjeta A debe mostrar los campos de la que tenga.
6. **Entrega en dos fases:**

   | Fase | Contenido |
   |---|---|
   | P0a | Login y `me`, captura de contacto en el bot, tableros A y B, cambio de etapa, detalle, transcripción y timeline |
   | P0b | Cierre con guía, tracking interno, dashboard, tareas, notificaciones y búsqueda RAG |

## Qué necesitamos que confirmen

| # | Pregunta | Nuestra propuesta |
|---|---|---|
| **1** | **¿Cuáles son las etapas exactas del tablero de ventas?** Es lo más urgente | `NUEVA_COTIZACION` → `EN_CONTACTO` → `DATOS_PENDIENTES` → `VENTA_CERRADA` → `ENTREGADO_A_AGENCIA` → `CERRADO`, más `DESCARTADO` (con motivo) |
| 2 | ¿Quién captura el destinatario al cerrar la venta? | El asesor, en un **formulario de cierre** nuevo en la UI (nombre, teléfono y dirección) |
| 3 | Si el cliente pide un asesor después de cotizar, ¿se crea un lead en lugar de un caso? | Sí. El lenguaje legal o muy negativo sigue siendo caso |
| 4 | ¿Con qué motor cotiza el chat? | Sigue con `provisional-v1`: la matriz de perfiles de prueba solo cubre 5 destinos |
| 5 | ¿Cómo se mapea la prioridad Alta/Media/Baja? | La que propusieron (§10.2 de su informe) |
| 6 | ¿Qué pasa con un cliente que no deja teléfono? | La cotización queda sin lead. Un caso urgente se muestra en B como "sin contacto" |
| 7 | ¿Se mantiene "Recolección"? | Se quita de la UI hasta que negocio la defina como componente de la tarifa |
| 8 | ¿Qué estado tiene un envío anulado? | `CANCELADO` (logística), nunca borrado |
| 9 | ¿Por qué canal va el seguimiento automático? | Correo. Solo afecta a P1, así que puede esperar |

Con la respuesta a la pregunta 1 (y, si es posible, a la 2 y la 3) empezamos P0a.
