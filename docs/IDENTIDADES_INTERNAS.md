# Identidades internas (CR-002, contrato 1.4.0)

Usuarios internos de TransLogix (asesores, supervisores, operadores logísticos y revisores
especializados) con un token personal. Permiten registrar **quién** hizo cada cambio, como exigen la
gestión comercial (CR-004) y la logística (CR-005). Esta versión no añade endpoints: entrega la
identidad, la autenticación, los roles, el CLI y la auditoría. Las decisiones están en
`contracts/DECISIONS.md` §6.

## Operación

```bash
cd backend
uv run alembic upgrade head      # migración 0003: staff_users, staff_tokens, api_audit_events.staff_user_id
uv run python -m app.staff.cli create-user --email asesora@translogix.co --nombre "Asesora" --role ASESOR
uv run python -m app.staff.cli issue-token --user-id <user_id> --expires-in-days 90
```

`issue-token` imprime el token completo **una sola vez**; solo se guarda su SHA-256. Otros comandos:

| Comando | Para qué |
|---|---|
| `show --user-id` | Ver el usuario, sus roles y sus tokens |
| `set-roles --user-id --role …` | Cambiar los roles; aplica en la siguiente petición |
| `set-status --user-id --estado INACTIVE` | Desactivar a la persona; sus tokens dejan de servir de inmediato |
| `revoke-token --token-id` | Revocar un token |

## Reglas

| Situación | Respuesta |
|---|---|
| Token ausente, malformado, desconocido, falsificado, revocado, vencido, o de un usuario inactivo | 401 `NO_AUTENTICADO` (la misma respuesta para todos los casos) |
| Rol insuficiente | 403 `NO_AUTORIZADO` con `details.roles_requeridos` |
| API key B2B (`tlx_…`) en una ruta interna, o token interno (`tlxs_…`) en una ruta B2B | 401 |

- Roles: `ASESOR`, `SUPERVISOR`, `OPERADOR_LOGISTICO` y `REVISOR_ESPECIALIZADO`. Cada usuario tiene al
  menos uno.
- La vigencia del token va de 1 a 365 días.
- Cada fallo se audita en `api_audit_events` con la acción `auth.failed`; el token nunca se registra.

## Uso en las rutas internas (CR-004 / CR-005)

```python
from app.contracts.staff import StaffRole
from app.staff.api.dependencies import staff_authenticated

Asesor = Annotated[StaffContext, Depends(staff_authenticated(StaffRole.ASESOR, StaffRole.SUPERVISOR))]
```

`StaffContext.staff_user_id` es el valor que va en `performed_by` y `changed_by`. Las reglas finas, como
que solo el asesor asignado actúe sobre su gestión, se aplican en cada caso de uso.

## Pruebas

`tests/staff/` contiene 15 pruebas:

- **Unitarias:**
  - formato del token y separación de familias;
  - normalización del correo;
  - verificación de roles.
- **Integración con PostgreSQL**, mediante una ruta de prueba protegida por la dependencia real:
  - token válido, con `last_used_at` y el token guardado solo como hash;
  - cada caso de 401;
  - 403 por rol, y cambio de rol aplicado en la siguiente petición;
  - token interno rechazado en una ruta B2B;
  - auditoría sin credenciales;
  - reglas de administración: correo duplicado o inválido, sin roles, vigencia fuera de rango y usuario
    inactivo.
