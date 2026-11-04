# Contrato 2.0.0: qué cambia para el frontend

**Es una versión MAJOR**: dos operaciones ya no aceptan llamadas sin la identidad del usuario. Lo
demás es aditivo. Tipos regenerados en `contracts/types/api.d.ts` y OpenAPI en
`contracts/openapi.yaml`.

**Estado del despliegue:** el código 2.0.0 está listo, pero el servidor del túnel (`:8000`) sigue en
**1.7.0** hasta que confirmen que pueden adaptar estos dos puntos. Avísennos y lo publicamos.

## 1. Cambios obligatorios

### 1.1 Historial del chat: `GET /api/v1/chat/sessions/{session_id}/history`

Ahora hay que enviar **`X-Client-Id`**: el mismo `client_id` (UUID del navegador) que ya mandan en el
*body* de `POST /chat/messages`.

```ts
await fetch(`${BASE}/chat/sessions/${sessionId}/history`, {
  headers: { "X-Client-Id": clientId },
});
```

| Caso | Antes | Ahora |
|---|---|---|
| Sin `X-Client-Id` | 200 | **401 `NO_AUTENTICADO`** |
| `X-Client-Id` de otra persona, o sesión inexistente | 404 | 404, sin cambio. No revela si la sesión existe |
| Panel interno con token de ADMIN | — | 200 (cualquier sesión) |
| Panel interno con token de REVIEWER | — | 200 si la sesión tiene un caso en su bandeja; si no, 404 |
| API key de un cliente B2B | — | 403 |

### 1.2 Adjuntos: `POST /api/v1/attachments`

Ahora exige **`X-Client-Id`** y el campo de formulario **`session_id`**, que debe ser una conversación
de web chat de ese mismo usuario. Por eso hay que haber enviado al menos un mensaje antes de subir un
archivo.

```ts
const form = new FormData();
form.append("file", file);
form.append("session_id", sessionId);          // obligatorio desde 2.0.0
form.append("declared_doc_code", "FACTURA_COMERCIAL");  // opcional, como antes
await fetch(`${BASE}/attachments`, { method: "POST", body: form, headers: { "X-Client-Id": clientId } });
```

| Caso | Respuesta |
|---|---|
| Sin `X-Client-Id` | 401 |
| Sin `session_id` | 422 |
| Sesión de otro usuario | 404 |
| Más de 10 subidas por minuto por usuario | **429 `LIMITE_DE_TASA`** |
| PDF incompleto o dañado (sin `%%EOF` final) | 422 |
| Más de 10 MiB | 413 (sin cambio) |

En `POST /chat/messages` solo se pueden referenciar `attachments` subidos por ese mismo usuario. El id
de otro usuario responde 404 con `details.attachment_ids`.

## 2. Cambios aditivos (no rompen nada)

| Cambio | Dónde | Uso sugerido |
|---|---|---|
| `estado_revision`: `NO_REQUERIDA`, `PENDIENTE`, `APROBADA` o `RECHAZADA` | Cada resultado de `POST /shipment-batches` y de `GET /shipment-batches/{id}/items` | Mostrar un aviso cuando el envío espera revisión especializada. La mercancía peligrosa nace `PENDIENTE` y solo un REVIEWER la aprueba o la rechaza |
| `503 SERVICIO_SATURADO` con `Retry-After: 5` | Cualquier operación salvo `/health` | Reintentar tras los segundos indicados; mensaje "alta demanda, intenta de nuevo" |
| Nuevo esquema de seguridad `ClientId` (cabecera `X-Client-Id`) | OpenAPI | Lo generan los tipos; no requiere más cambios |

## 3. Sin cambios

- **Endpoints sin cambios:**
  - chat (`POST /chat/messages`), rastreo, cotización y PQRS;
  - métricas, `/health` y `/ready`;
  - roles y tokens (ADMIN, REVIEWER y CLIENT), con las cuentas de demo ya entregadas;
  - `X-Admin-Token`.
- **CORS:** acepta `X-Client-Id` desde antes, así que no hace falta cambiar nada.

## 4. Checklist para el front

1. Enviar `X-Client-Id` en la carga del historial.
2. Enviar `X-Client-Id` y `session_id` al subir adjuntos, y deshabilitar la subida hasta que exista
   `session_id`.
3. Tratar 401, 404, 429 y 422 en la subida de adjuntos.
4. Mostrar `estado_revision` en el panel de lotes (cliente) y en el de mercancía peligrosa (revisor).
5. Tratar el 503 `SERVICIO_SATURADO` como un error temporal que se puede reintentar.
6. Confirmarnos cuándo publicar 2.0.0 en el túnel.

## 5. Novedades de 2.1.0 (aditivas, opcionales)

- **Descargar un adjunto:**
  - `GET /api/v1/attachments/{attachment_id}/content`, con la misma cabecera `X-Client-Id` que la
    subida (o el token de ADMIN o REVIEWER). Devuelve los bytes; muéstrenlos con un *blob URL*.
  - `GET /api/v1/attachments/{attachment_id}/link`: solo si el backend usa S3. Devuelve
    `{url, expira_en_segundos: 60}`, una URL prefirmada que se abre directamente sin cabeceras.
    Con almacenamiento local responde 503 `NO_CONFIGURADO`.
- **Reindexación asíncrona (panel ADMIN):**
  - `POST /api/v1/admin/rag/reindex` con `{"asincrono": true}` → 202 `{job_id, estado}`;
  - consulten `GET /api/v1/admin/rag/jobs/{job_id}` hasta `DONE` o `DEAD_LETTER`.
- **Error nuevo:** `503 ALMACENAMIENTO_NO_DISPONIBLE` al subir o bajar un archivo. Es temporal: se
  puede reintentar.

