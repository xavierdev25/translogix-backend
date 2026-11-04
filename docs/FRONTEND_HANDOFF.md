# TransLogix AI: Handoff backend → frontend

Documento de integración para diseño y frontend: con él se debería poder conectar la UI sin leer el código.
Todo se extrajo del código y de la ejecución real del backend el 2026-11-03:

- **Endpoints y tipos:** OpenAPI exportado de la app real (`docs/openapi.json`).
- **Respuestas JSON:** capturadas al llamar a la API real dentro de una transacción de BD que
  luego se revirtió, de modo que la auditoría no dejó datos. Los JSON completos están en
  `docs/api-examples/`.
- **Contratos de origen:** los modelos Pydantic de `backend/app/contracts/` (contrato 1.2.0).

Leyenda de estado: **PROBADO** = implementado y ejecutado contra la API real ·
**IMPLEMENTADO** = existe en código pero no se ejecutó · **SIMULADO** = funciona, pero no habla
con el proveedor real · **PARCIAL** = funciona con limitaciones · **PENDIENTE** = no existe.

Más ejemplos de flujo completo en `docs/FRONTEND_INTEGRATION_EXAMPLES.md`; el estado detallado
del backend, en `docs/BACKEND_CURRENT_STATE.md`.

---

## A. Información general

| Ítem | Valor |
|---|---|
| Base URL local | `http://localhost:8000` |
| Prefijo de la API | `/api/v1` (`/health` y `/ready` van sin prefijo) |
| Formato | JSON UTF-8, idioma `es-CO` |
| Fechas | ISO-8601 con offset de Bogotá, p. ej. `2026-11-03T16:00:00-05:00` |
| Dinero | Entero en COP (`190975`). Formatear en la UI como `$190.975` |
| IDs | UUID v4 en string; `numero_guia` con el formato `TLX-123456-CO` |
| Documentación interactiva | `GET /docs` (Swagger UI) y `GET /openapi.json`, ambos **200** en local |
| `/health` | **200** `{"status":"ok"}`: el proceso vive |
| `/ready` | **200** si BD, Ollama e índice RAG están listos; **503** con detalle si no |
| Trazabilidad | Header `X-Request-ID` opcional en la petición; la respuesta siempre lo trae, y los errores lo repiten en `error.request_id` |

### Cómo iniciar el backend

```bash
# Requisitos: Python 3.12 + uv, PostgreSQL 16+ con pgvector, Ollama con los modelos de abajo.
cd ~/Desktop/translogix-ai
cp -n .env.example .env          # luego completar los valores (ver tabla)
cd backend
uv sync
uv run alembic upgrade head      # crea las tablas y la extensión vector
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Primera vez, para que el RAG responda (requiere el token de administración):

```bash
TOKEN=$(grep '^ADMIN_TOKEN=' ../.env | cut -d= -f2-)
curl -s -X POST localhost:8000/api/v1/admin/rag/reindex -H "X-Admin-Token: $TOKEN" \
  -H 'Content-Type: application/json' -d '{}'
curl -s localhost:8000/ready     # → "status":"ready"
```

### Dependencias

| Dependencia | Uso | Si falta |
|---|---|---|
| PostgreSQL + pgvector (`DATABASE_URL`) | Sesiones, mensajes, handoffs, PQRS, cotizaciones, índice RAG | La app arranca, pero los endpoints con BD fallan con 500; `/ready` → `database: "fail"` |
| Ollama (`OLLAMA_BASE_URL`) + `qwen3:8b` | Redacción de respuestas RAG y clasificación de intención ambigua | Tracking, cotización, PQRS y escalamiento **siguen funcionando** (son deterministas). Las preguntas documentales responden "no puedo consultar la base documental" con la opción de asesor |
| `qwen3-embedding:0.6b` (dimensión 1024) | Embeddings RAG | Igual que arriba |
| Datos en `data/source/` | TMS simulado, tarifas, cobertura, manuales, reglas | La app no arranca |

### Variables que el frontend debe conocer

| Variable | Valor local | Efecto en el frontend |
|---|---|---|
| `CORS_ALLOW_ORIGINS` | `http://localhost:5173` | Lista separada por comas. **Debe incluir el origen exacto de la UI** (esquema, host y puerto); si no, el navegador bloquea las peticiones. Vacía = ningún origen permitido |
| `ADMIN_TOKEN` | secreto | Se necesita para la bandeja del asesor y el reindex. **Nunca debe ir en el bundle del cliente final** |
| `SESSION_TTL_MINUTES_WEB_CHAT` | 30 | Minutos de inactividad tras los que una sesión de chat expira |
| `CHAT_RATE_LIMIT_PER_MINUTE` | 30 | Mensajes por minuto por usuario; por encima, 429 |
| `ATTACHMENTS_MAX_BYTES` | 10485760 (10 MB) | Tamaño máximo de un adjunto |

### CORS

Configuración real: `allow_origins = CORS_ALLOW_ORIGINS`, métodos `GET, POST, PATCH`, headers
permitidos `Content-Type, X-Request-ID, X-Admin-Token, X-Client-Id`, **sin** `allow_credentials`
(no hay cookies) y **sin** `expose_headers`. Por eso el JS de otro origen **no puede leer** el header
de respuesta `X-Request-ID`. En los errores, usar `error.request_id` del cuerpo.

---

## B. Inventario de endpoints

18 operaciones en 16 rutas. Coinciden 1:1 (método, ruta y `operationId`) con el contrato; se
verificó comparando el OpenAPI real con el de `app/contracts/api_spec.py`. No hay más.

| # | Método | Ruta | Módulo | Descripción | Auth | Estado |
|---|---|---|---|---|---|---|
| 1 | `GET` | `/health` | Operación | Proceso vivo | — | PROBADO |
| 2 | `GET` | `/ready` | Operación | Dependencias listas (BD, Ollama, índice RAG); 503 si alguna falla | — | PROBADO |
| 3 | `GET` | `/api/v1/tracking/{guia}` | Tracking | Vista segura de una guía del TMS simulado | — | PROBADO |
| 4 | `POST` | `/api/v1/cotizar` | Cotización | Cotización determinista (fórmula provisional-v1) | — | PROBADO |
| 5 | `POST` | `/api/v1/pqrs/crear-ticket` | PQRS | Crea ticket con elegibilidad, checklist, liquidación y escalamiento | — | PROBADO |
| 6 | `POST` | `/api/v1/attachments` | Adjuntos | Sube un PDF o JPG (multipart) y devuelve attachment_id | — | PROBADO (PDF) |
| 7 | `POST` | `/api/v1/chat/messages` | Chat / Orquestador IA | Envía un mensaje del Web Chat y devuelve la respuesta | — | PROBADO |
| 8 | `GET` | `/api/v1/chat/sessions/{session_id}/history` | Chat / Sesiones | Historial cronológico de una sesión | `X-Client-Id` (opcional, ver Seguridad) | PROBADO |
| 9 | `GET` | `/api/v1/channels/whatsapp/webhook` | WhatsApp | Reto de verificación del webhook de Meta | `hub.verify_token` | PARCIAL (sin credenciales → 503) |
| 10 | `POST` | `/api/v1/channels/whatsapp/webhook` | WhatsApp | Webhook real de Meta con firma HMAC | `X-Hub-Signature-256` | IMPLEMENTADO, no probado (sin credenciales → 503) |
| 11 | `POST` | `/api/v1/channels/whatsapp/simulate` | WhatsApp | Simulación: mismo cuerpo que Meta, nada se envía | — | SIMULADO |
| 12 | `POST` | `/api/v1/channels/pbx/turns` | PBX | Turno de llamada por transcripción (sin audio) | — | SIMULADO (audio_ref → 503) |
| 13 | `POST` | `/api/v1/channels/pbx/simulate` | PBX | Atajo de simulación de un turno | — | SIMULADO |
| 14 | `GET` | `/api/v1/handoffs` | Handoff | Bandeja del asesor (filtro por estado) | `X-Admin-Token` | PROBADO |
| 15 | `GET` | `/api/v1/handoffs/{handoff_id}` | Handoff | Detalle y payload con historial (sin PII) | `X-Admin-Token` | PROBADO |
| 16 | `PATCH` | `/api/v1/handoffs/{handoff_id}` | Handoff | PENDING → ASSIGNED → CLOSED | `X-Admin-Token` | PROBADO |
| 17 | `GET` | `/api/v1/metrics/channels` | Métricas | Conversaciones, mensajes y handoffs por canal | — | PROBADO |
| 18 | `POST` | `/api/v1/admin/rag/reindex` | RAG (admin) | Reindexa el corpus documental (idempotente) | `X-Admin-Token` | PROBADO |

Endpoints que **no existen** (no los busque la UI): WebSocket de chat o de bandeja, un endpoint
para que el asesor escriba al cliente, login o usuarios, listado de cotizaciones, consulta de
tickets PQRS por id, y consulta RAG directa fuera del chat.

---

## C. Contratos de cada endpoint

Notas comunes:

- Todo error usa el sobre `ErrorEnvelope` (sección G).
- Los ejemplos son respuestas **reales**. Las listas de más de 3 elementos y los textos de más de
  400 caracteres aparecen recortados con `…`; la versión completa está en
  `docs/api-examples/<clave>.json`.
- En los cURL, `$ADMIN_TOKEN` es el valor de `ADMIN_TOKEN` del `.env`.

### C1. `GET /health`

Estado del proceso.

- **Módulo:** Operación · **Estado:** PROBADO · **operationId:** `getHealth`
- **Autenticación:** —
- **Headers:** ninguno obligatorio; `X-Request-ID` opcional (la respuesta siempre lo devuelve).

**Parámetros**

Sin parámetros.

**Body:** Sin cuerpo.

**Respuestas**

- `200` → `HealthResponse`
- `500` `ERROR_INTERNO` → `ErrorEnvelope`

**Ejemplos reales**

**health** — HTTP `200`

```bash
curl -s -X GET 'http://localhost:8000/health'
```

Response:
```json
{
  "status": "ok"
}
```


---

### C2. `GET /ready`

Dependencias listas (BD, Ollama, índice).

- **Módulo:** Operación · **Estado:** PROBADO · **operationId:** `getReadiness`
- **Autenticación:** —
- **Headers:** ninguno obligatorio; `X-Request-ID` opcional (la respuesta siempre lo devuelve).

**Parámetros**

Sin parámetros.

**Body:** Sin cuerpo.

**Respuestas**

- `200` → `ReadinessResponse`
- `503` → `ReadinessResponse` con `status: "not_ready"`
- `500` `ERROR_INTERNO` → `ErrorEnvelope`

**Ejemplos reales**

**ready** — HTTP `200`

```bash
curl -s -X GET 'http://localhost:8000/ready'
```

Response:
```json
{
  "status": "ready",
  "checks": {
    "database": "ok",
    "ollama": "ok",
    "rag_index": "ok"
  }
}
```


---

### C3. `GET /api/v1/tracking/{guia}`

Rastreo de guía (vista segura para cliente). Oficial (PDF). Acepta formas normalizables (`tlx 984021 co`, `TLX984021CO`).

- **Módulo:** Tracking · **Estado:** PROBADO · **operationId:** `getTracking`
- **Autenticación:** —
- **Headers:** ninguno obligatorio; `X-Request-ID` opcional (la respuesta siempre lo devuelve).

**Parámetros**

| Parámetro | En | Obligatorio | Tipo |
|---|---|---|---|
| `guia` | path | sí | `string` |

**Body:** Sin cuerpo.

**Respuestas**

- `200` → `TrackingResponse`
- `422` `GUIA_FORMATO_INVALIDO` → `ErrorEnvelope`
- `404` `GUIA_NO_ENCONTRADA` → `ErrorEnvelope`
- `422` `VALIDACION` → `ErrorEnvelope` (campos inválidos)
- `500` `ERROR_INTERNO` → `ErrorEnvelope`

**Ejemplos reales**

**tracking.transito** — HTTP `200`

```bash
curl -s -X GET 'http://localhost:8000/api/v1/tracking/TLX-984021-CO'
```

Response:
```json
{
  "numero_guia": "TLX-984021-CO",
  "estado_actual": {
    "codigo": "EN_TRANSITO",
    "etiqueta": "En tránsito",
    "explicacion": "Tu envío va en carretera hacia la ciudad de destino."
  },
  "origen_ciudad": "Bogotá D.C.",
  "destino_ciudad": "Medellín",
  "tipo_servicio": "Transporte Cadena de Frío (2°C a 8°C)",
  "fecha_despacho": "2026-11-02T14:30:00-05:00",
  "fecha_estimada_entrega": "2026-11-03T16:00:00-05:00",
  "ubicacion_referencia": "Paso de Montaña Alto de la Línea - Sentido Bogotá-Ibague-Medellín",
  "eventos": [
    {
      "timestamp": "2026-11-02T11:15:00-05:00",
      "estado": "SOLICITUD_GENERADA",
      "etiqueta": "Solicitud generada",
      "ubicacion": "Portal Web Clientes TransLogix",
      "descripcion_cliente": "Orden de despacho creada y guía generada exitosamente."
    },
    {
      "timestamp": "2026-11-02T14:30:00-05:00",
      "estado": "RECOGIDO_EN_ORIGEN",
      "etiqueta": "Recogido en origen",
      "ubicacion": "Zona Franca Fontibón, Bogotá D.C",
      "descripcion_cliente": "Carga inspeccionada, temperatura validada en 4.2°C y precinto de seguridad #TLX-8841 aplicado."
    },
    {
      "timestamp": "2026-11-02T18:45:00-05:00",
      "estado": "EN_BODEGA_ORIGEN",
      "etiqueta": "En bodega de origen",
      "ubicacion": "Hub Central Zona Franca Fontibón",
      "descripcion_cliente": "Consolidación en camión refrigerado."
    },
    "… (2 elementos más; ver docs/api-examples)"
  ],
  "prueba_entrega": null,
  "flags": {
    "requiere_refrigeracion": true,
    "mercancia_peligrosa": false
  },
  "ultima_temperatura_c": 3.8,
  "fuente": {
    "sistema": "TMS_SIMULADO",
    "consultado_en": "2026-11-03T13:17:28.959737-05:00"
  }
}
```

**tracking.404** — HTTP `404`

```bash
curl -s -X GET 'http://localhost:8000/api/v1/tracking/TLX-000000-CO'
```

Response:
```json
{
  "error": {
    "code": "GUIA_NO_ENCONTRADA",
    "message": "No encontramos una guía con ese número. Verifica que esté bien escrita.",
    "details": {
      "numero_guia": "TLX-000000-CO"
    },
    "request_id": "6ca49e60-08ac-41fc-ad19-c20b304d4d9d"
  }
}
```

**tracking.422** — HTTP `422`

```bash
curl -s -X GET 'http://localhost:8000/api/v1/tracking/ABC-123'
```

Response:
```json
{
  "error": {
    "code": "GUIA_FORMATO_INVALIDO",
    "message": "El número de guía no tiene un formato válido. Debe verse así: TLX-123456-CO.",
    "details": {
      "guia": "ABC-123"
    },
    "request_id": "1c3445c9-8532-44d6-a8d8-0166a7aaa6f5"
  }
}
```


---

### C4. `POST /api/v1/cotizar`

Cotización de flete (fórmula provisional-v1). Oficial (PDF). Los valores son salidas de `provisional-v1`, no tarifas oficiales.

- **Módulo:** Cotización · **Estado:** PROBADO · **operationId:** `createQuote`
- **Autenticación:** —
- **Headers:** `Content-Type: application/json`; `X-Request-ID` opcional (la respuesta siempre lo devuelve).

**Parámetros**

Sin parámetros.

**Body:** `application/json` → `CotizacionRequest` (obligatorio).

**Respuestas**

- `200` → `CotizacionResponse`
- `422` `RUTA_SIN_TARIFA` → `ErrorEnvelope`
- `422` `CIUDAD_NO_RECONOCIDA` → `ErrorEnvelope`
- `422` `SERVICIO_NO_DISPONIBLE` → `ErrorEnvelope`
- `422` `EXCEDE_LIMITE_COBERTURA` → `ErrorEnvelope`
- `422` `VALIDACION` → `ErrorEnvelope` (observado)
- `500` `ERROR_INTERNO` → `ErrorEnvelope`

**Ejemplos reales**

**cotizar** — HTTP `200`

```bash
curl -s -X POST 'http://localhost:8000/api/v1/cotizar' \
  -H 'Content-Type: application/json' \
  -d '{"origen": "Bogotá D.C.", "destino": "Medellín", "alto_cm": 40, "ancho_cm": 50, "largo_cm": 60, "peso_real_kg": 45.5, "valor_declarado_cop": 12500000, "requiere_cadena_frio": false, "mercancia_peligrosa": false}'
```

Request:
```json
{
  "origen": "Bogotá D.C.",
  "destino": "Medellín",
  "alto_cm": 40,
  "ancho_cm": 50,
  "largo_cm": 60,
  "peso_real_kg": 45.5,
  "valor_declarado_cop": 12500000,
  "requiere_cadena_frio": false,
  "mercancia_peligrosa": false
}
```

Response:
```json
{
  "cotizacion_id": "b758b92e-43f5-4620-a6ff-953893b09ba2",
  "ruta": {
    "codigo_trayecto": "TLX-BOG-MED-001",
    "origen": "Bogotá D.C.",
    "destino": "Medellín"
  },
  "calculo": {
    "peso_volumetrico_kg": 30.0,
    "peso_facturable_kg": 45.5,
    "tarifa_aplicada": {
      "tipo": "REAL",
      "cop_por_kg": 1450
    },
    "flete_minimo_cop": 18500,
    "flete_minimo_aplicado": false,
    "flete_base_cop": 65975,
    "seguro_cop": 125000,
    "recargos": [],
    "flete_total_cop": 190975
  },
  "tiempo_entrega_horas": 24,
  "advertencias": [],
  "supuestos": [
    "formula_version=provisional-v1",
    "seguro_minimo_fuente=pdf(2500)",
    "seguro_minimo_alternativo=csv(3500)"
  ],
  "formula_version": "provisional-v1"
}
```

**cotizar.ruta_sin_tarifa** — HTTP `422`

```bash
curl -s -X POST 'http://localhost:8000/api/v1/cotizar' \
  -H 'Content-Type: application/json' \
  -d '{"origen": "Barranquilla", "destino": "Bucaramanga", "alto_cm": 30, "ancho_cm": 30, "largo_cm": 30, "peso_real_kg": 10, "valor_declarado_cop": 1000000, "requiere_cadena_frio": false, "mercancia_peligrosa": false}'
```

Request:
```json
{
  "origen": "Barranquilla",
  "destino": "Bucaramanga",
  "alto_cm": 30,
  "ancho_cm": 30,
  "largo_cm": 30,
  "peso_real_kg": 10,
  "valor_declarado_cop": 1000000,
  "requiere_cadena_frio": false,
  "mercancia_peligrosa": false
}
```

Response:
```json
{
  "error": {
    "code": "RUTA_SIN_TARIFA",
    "message": "No tengo tarifa para esa ruta en la matriz disponible.",
    "details": {
      "origen": "Barranquilla",
      "destino": "Bucaramanga"
    },
    "request_id": "c298a917-a361-44f4-9570-c1cced3ee968"
  }
}
```

**cotizar.422** — HTTP `422`

```bash
curl -s -X POST 'http://localhost:8000/api/v1/cotizar' \
  -H 'Content-Type: application/json' \
  -d '{"origen": "Bogotá D.C.", "destino": "Medellín", "alto_cm": 40, "ancho_cm": 50, "largo_cm": 60, "peso_real_kg": 45.5, "valor_declarado_cop": "12500000", "requiere_cadena_frio": false, "mercancia_peligrosa": false}'
```

Request:
```json
{
  "origen": "Bogotá D.C.",
  "destino": "Medellín",
  "alto_cm": 40,
  "ancho_cm": 50,
  "largo_cm": 60,
  "peso_real_kg": 45.5,
  "valor_declarado_cop": "12500000",
  "requiere_cadena_frio": false,
  "mercancia_peligrosa": false
}
```

Response:
```json
{
  "error": {
    "code": "VALIDACION",
    "message": "Revisa los datos enviados: hay campos inválidos o incompletos.",
    "details": {
      "campos": [
        {
          "loc": [
            "body",
            "valor_declarado_cop"
          ],
          "msg": "Input should be a valid integer",
          "type": "int_type"
        }
      ]
    },
    "request_id": "74214023-abe1-4e9c-99e2-c2460701a238"
  }
}
```


---

### C5. `POST /api/v1/pqrs/crear-ticket`

Crea un ticket PQRS con elegibilidad, checklist y liquidación preliminar. Oficial (PDF). La liquidación es preliminar y no vinculante.

- **Módulo:** PQRS · **Estado:** PROBADO · **operationId:** `createPqrsTicket`
- **Autenticación:** —
- **Headers:** `Content-Type: application/json`; `X-Request-ID` opcional (la respuesta siempre lo devuelve).

**Parámetros**

Sin parámetros.

**Body:** `application/json` → `PqrsTicketRequest` (obligatorio).

**Respuestas**

- `201` → `PqrsTicketResponse`
- `404` `GUIA_NO_ENCONTRADA` → `ErrorEnvelope`
- `404` `NO_ENCONTRADO` → `ErrorEnvelope`
- `422` `VALIDACION` → `ErrorEnvelope` (campos inválidos)
- `500` `ERROR_INTERNO` → `ErrorEnvelope`

**Ejemplos reales**

**pqrs.siniestro** — HTTP `201`

```bash
curl -s -X POST 'http://localhost:8000/api/v1/pqrs/crear-ticket' \
  -H 'Content-Type: application/json' \
  -d '{"session_id": null, "tipo": "SINIESTRO", "numero_guia": "TLX-752190-CO", "tipo_novedad": "AVERIA_NO_APARENTE", "descripcion": "Al abrir las cajas encontramos herramientas dañadas; el empaque exterior no mostraba golpes.", "valor_reclamado_cop": 4000000, "documentos": [{"codigo": "FACTURA_COMERCIAL", "attachment_id": "1e80dc4a-c133-40e4-9063-bf4d812fafe3"}]}'
```

Request:
```json
{
  "session_id": null,
  "tipo": "SINIESTRO",
  "numero_guia": "TLX-752190-CO",
  "tipo_novedad": "AVERIA_NO_APARENTE",
  "descripcion": "Al abrir las cajas encontramos herramientas dañadas; el empaque exterior no mostraba golpes.",
  "valor_reclamado_cop": 4000000,
  "documentos": [
    {
      "codigo": "FACTURA_COMERCIAL",
      "attachment_id": "1e80dc4a-c133-40e4-9063-bf4d812fafe3"
    }
  ]
}
```

Response:
```json
{
  "ticket_id": "26d91d28-6a1e-47fb-977c-a670f2944c2b",
  "estado": "ESCALADO",
  "elegibilidad": {
    "dentro_de_plazo": true,
    "regla_aplicada": "AVERIA_NO_APARENTE_3_DIAS_HABILES",
    "fecha_limite": "2026-11-05",
    "nota": "días hábiles sin considerar festivos (calendario no configurado)",
    "fundamento": {
      "doc_code": "DOC-PQRS-2026-04",
      "section_path": "3. Tiempos y plazos legales de radicación"
    }
  },
  "checklist": [
    {
      "codigo": "GUIA",
      "obligatorio": true,
      "recibido": true
    },
    {
      "codigo": "FACTURA_COMERCIAL",
      "obligatorio": true,
      "recibido": true
    },
    {
      "codigo": "CARTA_RECLAMACION",
      "obligatorio": true,
      "recibido": false
    },
    "… (5 elementos más; ver docs/api-examples)"
  ],
  "faltantes": [
    "CARTA_RECLAMACION",
    "CERTIFICACION_BANCARIA",
    "FOTOS_EMPAQUE",
    "… (2 elementos más; ver docs/api-examples)"
  ],
  "liquidacion_preliminar": {
    "base_cop": 4000000,
    "deducible_pct": 5,
    "deducible_cop": 200000,
    "flete_devuelto": "PENDIENTE_REVISION_HUMANA",
    "estado": "PRELIMINAR_NO_VINCULANTE"
  },
  "escalamiento": {
    "escalate": true,
    "primary": {
      "id_criterio": "CRIT-003",
      "accion": "ESCALAR_A_DEPARTAMENTO_JURIDICO_Y_PQRS",
      "accion_raw": "ESCALAR_A_DEPARTAMENTO_JURIDICO_Y_PQRS",
      "evidencia": "valor_declarado_cop > 5000000 (contexto de reclamación)",
      "modo": "claim_context"
    },
    "triggers": [
      {
        "id_criterio": "CRIT-003",
        "accion": "ESCALAR_A_DEPARTAMENTO_JURIDICO_Y_PQRS",
        "accion_raw": "ESCALAR_A_DEPARTAMENTO_JURIDICO_Y_PQRS",
        "evidencia": "valor_declarado_cop > 5000000 (contexto de reclamación)",
        "modo": "claim_context"
      }
    ],
    "mensaje_cliente": "Dado que el valor reclamado supera el límite de trámite automático en chat ($5,000,000 COP), un gestor senior de PQRS tomará tu caso.",
    "rules_version": "1.0.2026",
    "config_snapshot": {
      "CRIT003_MODE": "claim_context",
      "CRIT005_MODE": "incident_context",
      "CONFIDENCE_THRESHOLD": "0.75",
      "PRIORITY_ORDER": "CRIT-005>CRIT-003>CRIT-002>CRIT-004>CRIT-001>INFO-INTENTOS",
      "omitidas": "CRIT-001:sin_respuesta_rag,INFO-INTENTOS:sin_dato"
    }
  }
}
```

**pqrs.peticion** — HTTP `201`

```bash
curl -s -X POST 'http://localhost:8000/api/v1/pqrs/crear-ticket' \
  -H 'Content-Type: application/json' \
  -d '{"session_id": null, "tipo": "PETICION", "numero_guia": "TLX-752190-CO", "tipo_novedad": null, "descripcion": "Solicito copia de la prueba de entrega de la guía.", "valor_reclamado_cop": null, "documentos": []}'
```

Request:
```json
{
  "session_id": null,
  "tipo": "PETICION",
  "numero_guia": "TLX-752190-CO",
  "tipo_novedad": null,
  "descripcion": "Solicito copia de la prueba de entrega de la guía.",
  "valor_reclamado_cop": null,
  "documentos": []
}
```

Response:
```json
{
  "ticket_id": "0ad5e935-aa99-4511-969a-50f4f6850f81",
  "estado": "ESCALADO",
  "elegibilidad": null,
  "checklist": [],
  "faltantes": [],
  "liquidacion_preliminar": null,
  "escalamiento": {
    "escalate": true,
    "primary": {
      "id_criterio": "CRIT-003",
      "accion": "ESCALAR_A_DEPARTAMENTO_JURIDICO_Y_PQRS",
      "accion_raw": "ESCALAR_A_DEPARTAMENTO_JURIDICO_Y_PQRS",
      "evidencia": "valor_declarado_cop > 5000000 (contexto de reclamación)",
      "modo": "claim_context"
    },
    "triggers": [
      {
        "id_criterio": "CRIT-003",
        "accion": "ESCALAR_A_DEPARTAMENTO_JURIDICO_Y_PQRS",
        "accion_raw": "ESCALAR_A_DEPARTAMENTO_JURIDICO_Y_PQRS",
        "evidencia": "valor_declarado_cop > 5000000 (contexto de reclamación)",
        "modo": "claim_context"
      }
    ],
    "mensaje_cliente": "Dado que el valor reclamado supera el límite de trámite automático en chat ($5,000,000 COP), un gestor senior de PQRS tomará tu caso.",
    "rules_version": "1.0.2026",
    "config_snapshot": {
      "CRIT003_MODE": "claim_context",
      "CRIT005_MODE": "incident_context",
      "CONFIDENCE_THRESHOLD": "0.75",
      "PRIORITY_ORDER": "CRIT-005>CRIT-003>CRIT-002>CRIT-004>CRIT-001>INFO-INTENTOS",
      "omitidas": "CRIT-001:sin_respuesta_rag,INFO-INTENTOS:sin_dato"
    }
  }
}
```


---

### C6. `POST /api/v1/attachments`

Sube un adjunto (PDF o JPG) y devuelve su attachment_id. Tipo validado por contenido; límite de tamaño configurable; el nombre de archivo del cliente no se conserva y el contenido no se registra en logs.

- **Módulo:** Adjuntos · **Estado:** PROBADO (PDF) · **operationId:** `uploadAttachment`
- **Autenticación:** —
- **Headers:** `Content-Type: multipart/form-data`; `X-Request-ID` opcional (la respuesta siempre lo devuelve).

**Parámetros**

Sin parámetros.

**Body:** `multipart/form-data` con campos `file`, `declared_doc_code` (opcional), `session_id` (opcional).

**Respuestas**

- `201` → `AttachmentUploadResponse`
- `404` `NO_ENCONTRADO` → `ErrorEnvelope`
- `422` `VALIDACION` → `ErrorEnvelope` (campos inválidos)
- `500` `ERROR_INTERNO` → `ErrorEnvelope`

**Ejemplos reales**

**attachments** — HTTP `201`

```bash
curl -s -X POST 'http://localhost:8000/api/v1/attachments' \
  -F 'declared_doc_code=FACTURA_COMERCIAL' \
  -F 'file=@factura.pdf;type=application/pdf'
```

Response:
```json
{
  "attachment_id": "1e80dc4a-c133-40e4-9063-bf4d812fafe3",
  "kind": "DOCUMENT",
  "mime_type": "application/pdf",
  "size_bytes": 22,
  "declared_doc_code": "FACTURA_COMERCIAL",
  "sha256": "1d8926bbed2a7919dbb3408182acaf7a32f47ce00293589898b8a81c579ff11b"
}
```


---

### C7. `POST /api/v1/chat/messages`

Envía un mensaje del Web Chat y devuelve la respuesta del núcleo. Idempotente por `message_id`: un reenvío devuelve la respuesta ya generada.

- **Módulo:** Chat / Orquestador IA · **Estado:** PROBADO · **operationId:** `postChatMessage`
- **Autenticación:** —
- **Headers:** `Content-Type: application/json`; `X-Request-ID` opcional (la respuesta siempre lo devuelve).

**Parámetros**

Sin parámetros.

**Body:** `application/json` → `ChatMessageRequest` (obligatorio).

**Respuestas**

- `200` → `ChatMessageResponse`
- `404` `NO_ENCONTRADO` → `ErrorEnvelope`
- `429` `LIMITE_DE_TASA` → `ErrorEnvelope`
- `422` `VALIDACION` → `ErrorEnvelope` (observado)
- `500` `ERROR_INTERNO` → `ErrorEnvelope`

**Ejemplos reales**

**chat.saludo_nueva_sesion** — HTTP `200`

```bash
curl -s -X POST 'http://localhost:8000/api/v1/chat/messages' \
  -H 'Content-Type: application/json' \
  -d '{"client_id": "ca85aa16-726d-41d4-b466-b539344d7e56", "message_id": "6ac9ecf4-42fa-4593-9a5a-56b7c6a0cc57", "text": "Hola"}'
```

Request:
```json
{
  "client_id": "ca85aa16-726d-41d4-b466-b539344d7e56",
  "message_id": "6ac9ecf4-42fa-4593-9a5a-56b7c6a0cc57",
  "text": "Hola"
}
```

Response:
```json
{
  "reply": {
    "session_id": "e0d4abfc-cb6a-4c30-9f56-2360d6430435",
    "in_reply_to": "6ac9ecf4-42fa-4593-9a5a-56b7c6a0cc57",
    "reply_id": "7f5a1e99-c6dc-4d4f-8851-243961f57978",
    "channel": "WEB_CHAT",
    "status": "AWAITING_USER",
    "blocks": [
      {
        "type": "TEXT",
        "text": "¡Hola! Soy el asistente de TransLogix. Puedo rastrear una guía, cotizar un envío, resolver dudas sobre mercancías especiales o ayudarte a radicar una PQRS."
      },
      {
        "type": "QUICK_REPLIES",
        "options": [
          {
            "id": "rastrear",
            "title": "Rastrear una guía"
          },
          {
            "id": "cotizar",
            "title": "Cotizar un envío"
          },
          {
            "id": "asesor",
            "title": "Hablar con un asesor"
          }
        ]
      }
    ],
    "meta": {
      "trace_id": "ee15da20-34c5-48ee-816c-f47679888426",
      "intent": "SALUDO",
      "confidence_score": null,
      "confidence_method": null,
      "escalation": {
        "escalate": false,
        "primary": null,
        "triggers": [],
        "mensaje_cliente": null,
        "rules_version": "1.0.2026",
        "config_snapshot": {
          "CRIT003_MODE": "claim_context",
          "CRIT005_MODE": "incident_context",
          "CONFIDENCE_THRESHOLD": "0.75",
          "PRIORITY_ORDER": "CRIT-005>CRIT-003>CRIT-002>CRIT-004>CRIT-001>INFO-INTENTOS",
          "omitidas": "CRIT-001:sin_respuesta_rag,CRIT-003:sin_valores,CRIT-004:sin_datos_cuenta,CRIT-005:sin_datos_de_carga,INFO-INTENTOS:sin_dato"
        }
      },
      "services_called": [
        "escalation"
      ],
      "evidence_ids": [],
      "llm_used": false,
      "delivery_mode": "SIMULATED"
    },
    "created_at": "2026-11-03T13:17:28.994065-05:00"
  }
}
```

**chat.reenvio_idempotente** — HTTP `200`

```bash
curl -s -X POST 'http://localhost:8000/api/v1/chat/messages' \
  -H 'Content-Type: application/json' \
  -d '{"client_id": "ca85aa16-726d-41d4-b466-b539344d7e56", "message_id": "6ac9ecf4-42fa-4593-9a5a-56b7c6a0cc57", "text": "Hola"}'
```

Request:
```json
{
  "client_id": "ca85aa16-726d-41d4-b466-b539344d7e56",
  "message_id": "6ac9ecf4-42fa-4593-9a5a-56b7c6a0cc57",
  "text": "Hola"
}
```

Response:
```json
{
  "reply": {
    "session_id": "e0d4abfc-cb6a-4c30-9f56-2360d6430435",
    "in_reply_to": "6ac9ecf4-42fa-4593-9a5a-56b7c6a0cc57",
    "reply_id": "7f5a1e99-c6dc-4d4f-8851-243961f57978",
    "channel": "WEB_CHAT",
    "status": "AWAITING_USER",
    "blocks": [
      {
        "type": "TEXT",
        "text": "¡Hola! Soy el asistente de TransLogix. Puedo rastrear una guía, cotizar un envío, resolver dudas sobre mercancías especiales o ayudarte a radicar una PQRS."
      },
      {
        "type": "QUICK_REPLIES",
        "options": [
          {
            "id": "rastrear",
            "title": "Rastrear una guía"
          },
          {
            "id": "cotizar",
            "title": "Cotizar un envío"
          },
          {
            "id": "asesor",
            "title": "Hablar con un asesor"
          }
        ]
      }
    ],
    "meta": {
      "trace_id": "ee15da20-34c5-48ee-816c-f47679888426",
      "intent": "SALUDO",
      "confidence_score": null,
      "confidence_method": null,
      "escalation": {
        "escalate": false,
        "primary": null,
        "triggers": [],
        "mensaje_cliente": null,
        "rules_version": "1.0.2026",
        "config_snapshot": {
          "omitidas": "CRIT-001:sin_respuesta_rag,CRIT-003:sin_valores,CRIT-004:sin_datos_cuenta,CRIT-005:sin_datos_de_carga,INFO-INTENTOS:sin_dato",
          "CRIT003_MODE": "claim_context",
          "CRIT005_MODE": "incident_context",
          "PRIORITY_ORDER": "CRIT-005>CRIT-003>CRIT-002>CRIT-004>CRIT-001>INFO-INTENTOS",
          "CONFIDENCE_THRESHOLD": "0.75"
        }
      },
      "services_called": [
        "escalation"
      ],
      "evidence_ids": [],
      "llm_used": false,
      "delivery_mode": "SIMULATED"
    },
    "created_at": "2026-11-03T13:17:28.994065-05:00"
  }
}
```

**chat.sesion_ajena** — HTTP `404`

```bash
curl -s -X POST 'http://localhost:8000/api/v1/chat/messages' \
  -H 'Content-Type: application/json' \
  -d '{"client_id": "487447ce-ebd4-47e8-8afb-ef92a3669963", "session_id": "e0d4abfc-cb6a-4c30-9f56-2360d6430435", "message_id": "69c1cea8-d9ca-4f5c-ab90-f28db08d35ac", "text": "hola"}'
```

Request:
```json
{
  "client_id": "487447ce-ebd4-47e8-8afb-ef92a3669963",
  "session_id": "e0d4abfc-cb6a-4c30-9f56-2360d6430435",
  "message_id": "69c1cea8-d9ca-4f5c-ab90-f28db08d35ac",
  "text": "hola"
}
```

Response:
```json
{
  "error": {
    "code": "NO_ENCONTRADO",
    "message": "No encontramos el recurso solicitado.",
    "details": {
      "recurso": "session"
    },
    "request_id": "862ac9aa-7b4c-462a-83ed-8ee36601f28f"
  }
}
```

**chat.422_vacio** — HTTP `422`

```bash
curl -s -X POST 'http://localhost:8000/api/v1/chat/messages' \
  -H 'Content-Type: application/json' \
  -d '{"client_id": "ca85aa16-726d-41d4-b466-b539344d7e56", "message_id": "81487e34-6ebb-42b0-95f0-404bd6aa24b7", "text": "  "}'
```

Request:
```json
{
  "client_id": "ca85aa16-726d-41d4-b466-b539344d7e56",
  "message_id": "81487e34-6ebb-42b0-95f0-404bd6aa24b7",
  "text": "  "
}
```

Response:
```json
{
  "error": {
    "code": "VALIDACION",
    "message": "Revisa los datos enviados: hay campos inválidos o incompletos.",
    "details": {
      "campos": [
        {
          "loc": [
            "body"
          ],
          "msg": "Value error, a message needs text or at least one attachment",
          "type": "value_error"
        }
      ]
    },
    "request_id": "85a8b71f-8745-44a4-b63c-28e83e9b8ea2"
  }
}
```


---

### C8. `GET /api/v1/chat/sessions/{session_id}/history`

Historial de una sesión.

- **Módulo:** Chat / Sesiones · **Estado:** PROBADO · **operationId:** `getSessionHistory`
- **Autenticación:** `X-Client-Id` (opcional, ver Seguridad)
- **Headers:** ninguno obligatorio; `X-Client-Id` (opcional, ver Seguridad); `X-Request-ID` opcional (la respuesta siempre lo devuelve).

**Parámetros**

| Parámetro | En | Obligatorio | Tipo |
|---|---|---|---|
| `session_id` | path | sí | `string` |
| `X-Client-Id` | header | no | `string | null` |

**Body:** Sin cuerpo.

**Respuestas**

- `200` → `SessionHistory`
- `404` `NO_ENCONTRADO` → `ErrorEnvelope`
- `422` `VALIDACION` → `ErrorEnvelope` (campos inválidos)
- `500` `ERROR_INTERNO` → `ErrorEnvelope`

**Ejemplos reales**

**chat.history** — HTTP `200`

```bash
curl -s -X GET 'http://localhost:8000/api/v1/chat/sessions/e0d4abfc-cb6a-4c30-9f56-2360d6430435/history' \
  -H "X-Client-Id: ca85aa16-726d-41d4-b466-b539344d7e56"
```

Response:
```json
{
  "session_id": "e0d4abfc-cb6a-4c30-9f56-2360d6430435",
  "channel": "WEB_CHAT",
  "status": "AWAITING_USER",
  "messages": [
    {
      "message_id": "6ac9ecf4-42fa-4593-9a5a-56b7c6a0cc57",
      "direction": "INBOUND",
      "message_type": "TEXT",
      "text": "Hola",
      "blocks": null,
      "delivery_mode": "SIMULATED",
      "created_at": "2026-11-03T13:17:28.994065-05:00"
    },
    {
      "message_id": "7f5a1e99-c6dc-4d4f-8851-243961f57978",
      "direction": "OUTBOUND",
      "message_type": null,
      "text": "¡Hola! Soy el asistente de TransLogix. Puedo rastrear una guía, cotizar un envío, resolver dudas sobre mercancías especiales o ayudarte a radicar una PQRS.",
      "blocks": [
        {
          "type": "TEXT",
          "text": "¡Hola! Soy el asistente de TransLogix. Puedo rastrear una guía, cotizar un envío, resolver dudas sobre mercancías especiales o ayudarte a radicar una PQRS."
        },
        {
          "type": "QUICK_REPLIES",
          "options": [
            {
              "id": "rastrear",
              "title": "Rastrear una guía"
            },
            {
              "id": "cotizar",
              "title": "Cotizar un envío"
            },
            {
              "id": "asesor",
              "title": "Hablar con un asesor"
            }
          ]
        }
      ],
      "delivery_mode": "SIMULATED",
      "created_at": "2026-11-03T13:17:28.994065-05:00"
    },
    {
      "message_id": "20fac391-2d7c-4cf0-90c9-ac1c311f0bf5",
      "direction": "INBOUND",
      "message_type": "TEXT",
      "text": "¿Dónde está mi guía TLX-984021-CO?",
      "blocks": null,
      "delivery_mode": "SIMULATED",
      "created_at": "2026-11-03T13:17:29.004216-05:00"
    },
    "… (1 elementos más; ver docs/api-examples)"
  ]
}
```

**chat.history_ajeno** — HTTP `404`

```bash
curl -s -X GET 'http://localhost:8000/api/v1/chat/sessions/e0d4abfc-cb6a-4c30-9f56-2360d6430435/history' \
  -H "X-Client-Id: e3c6c417-87af-45b7-b743-24eeffb796f7"
```

Response:
```json
{
  "error": {
    "code": "NO_ENCONTRADO",
    "message": "No encontramos el recurso solicitado.",
    "details": {
      "recurso": "session"
    },
    "request_id": "85176565-368e-422a-ae2d-6044fbc0f0e5"
  }
}
```


---

### C9. `GET /api/v1/channels/whatsapp/webhook`

Verificación del webhook (reto de suscripción de Meta). Responde `hub.challenge` en texto plano si `hub.verify_token` coincide.

- **Módulo:** WhatsApp · **Estado:** PARCIAL (sin credenciales → 503) · **operationId:** `verifyWhatsAppWebhook`
- **Autenticación:** `hub.verify_token`
- **Headers:** ninguno obligatorio; `hub.verify_token`; `X-Request-ID` opcional (la respuesta siempre lo devuelve).

**Parámetros**

| Parámetro | En | Obligatorio | Tipo |
|---|---|---|---|
| `hub.mode` | query | sí | `string` |
| `hub.verify_token` | query | sí | `string` |
| `hub.challenge` | query | sí | `string` |

**Body:** Sin cuerpo.

**Respuestas**

- `200` → `ReadinessResponse`
- `403` `NO_AUTORIZADO` → `ErrorEnvelope`
- `503` `NO_CONFIGURADO` → `ErrorEnvelope` (observado)
- `422` `VALIDACION` → `ErrorEnvelope` (campos inválidos)
- `500` `ERROR_INTERNO` → `ErrorEnvelope`

**Ejemplos reales**

**whatsapp.verify_503** — HTTP `503`

```bash
curl -s -X GET 'http://localhost:8000/api/v1/channels/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=x&hub.challenge=123'
```

Response:
```json
{
  "error": {
    "code": "NO_CONFIGURADO",
    "message": "Esta función no está configurada en este entorno.",
    "details": {
      "variable": "WHATSAPP_VERIFY_TOKEN"
    },
    "request_id": "7386a794-5c50-4458-a328-60689c269595"
  }
}
```


---

### C10. `POST /api/v1/channels/whatsapp/webhook`

Recepción real de mensajes de WhatsApp (Meta Cloud API). Exige `X-Hub-Signature-256` (HMAC-SHA256 del cuerpo con `WHATSAPP_APP_SECRET`). El cuerpo es el de Meta; **validar contra la documentación vigente de Meta**.

- **Módulo:** WhatsApp · **Estado:** IMPLEMENTADO, no probado (sin credenciales → 503) · **operationId:** `receiveWhatsAppWebhook`
- **Autenticación:** `X-Hub-Signature-256`
- **Headers:** ninguno obligatorio; `X-Hub-Signature-256`; `X-Request-ID` opcional (la respuesta siempre lo devuelve).

**Parámetros**

| Parámetro | En | Obligatorio | Tipo |
|---|---|---|---|
| `X-Hub-Signature-256` | header | sí | `string` |

**Body:** Sin cuerpo.

**Respuestas**

- `200` → `WebhookAck`
- `401` `FIRMA_INVALIDA` → `ErrorEnvelope`
- `503` `NO_CONFIGURADO` → `ErrorEnvelope` (observado)
- `422` `VALIDACION` → `ErrorEnvelope` (campos inválidos)
- `500` `ERROR_INTERNO` → `ErrorEnvelope`

**Ejemplos reales**

**whatsapp.webhook_503** — HTTP `503`

```bash
curl -s -X POST 'http://localhost:8000/api/v1/channels/whatsapp/webhook' \
  -H "X-Hub-Signature-256: sha256=00" \
  -H 'Content-Type: application/json' \
  -d '{"object": "whatsapp_business_account", "entry": [{"id": "100000000000000", "changes": [{"field": "messages", "value": {"messaging_product": "whatsapp", "metadata": {"display_phone_number": "570000000000", "phone_number_id": "100000000000001"}, "contacts": [{"profile": {"name": "Cliente Demo"}, "wa_id": "573000000000"}], "messages": [{"from": "573000000000", "id": "wamid.SIMULATED-0001", "timestamp": "1762182000", "type": "text", "text": {"body": "¿Dónde está mi guía TLX-984021-CO?"}}]}}]}]}'
```

Request:
```json
{
  "object": "whatsapp_business_account",
  "entry": [
    {
      "id": "100000000000000",
      "changes": [
        {
          "field": "messages",
          "value": {
            "messaging_product": "whatsapp",
            "metadata": {
              "display_phone_number": "570000000000",
              "phone_number_id": "100000000000001"
            },
            "contacts": [
              {
                "profile": {
                  "name": "Cliente Demo"
                },
                "wa_id": "573000000000"
              }
            ],
            "messages": [
              {
                "from": "573000000000",
                "id": "wamid.SIMULATED-0001",
                "timestamp": "1762182000",
                "type": "text",
                "text": {
                  "body": "¿Dónde está mi guía TLX-984021-CO?"
                }
              }
            ]
          }
        }
      ]
    }
  ]
}
```

Response:
```json
{
  "error": {
    "code": "NO_CONFIGURADO",
    "message": "Esta función no está configurada en este entorno.",
    "details": {
      "variable": "WHATSAPP_APP_SECRET"
    },
    "request_id": "fa97f9c5-a3c5-47d4-8249-95d6b0aabd32"
  }
}
```


---

### C11. `POST /api/v1/channels/whatsapp/simulate`

Simulación de WhatsApp: mismo cuerpo que el webhook real, marcado SIMULATED.

- **Módulo:** WhatsApp · **Estado:** SIMULADO · **operationId:** `simulateWhatsApp`
- **Autenticación:** —
- **Headers:** `Content-Type: application/json`; `X-Request-ID` opcional (la respuesta siempre lo devuelve).

**Parámetros**

Sin parámetros.

**Body:** `application/json` → `WhatsAppWebhookPayload` (obligatorio).

**Respuestas**

- `200` → `WhatsAppSimulationResult`
- `422` `VALIDACION` → `ErrorEnvelope` (campos inválidos)
- `500` `ERROR_INTERNO` → `ErrorEnvelope`

**Ejemplos reales**

**whatsapp.simulate** — HTTP `200`

```bash
curl -s -X POST 'http://localhost:8000/api/v1/channels/whatsapp/simulate' \
  -H 'Content-Type: application/json' \
  -d '{"object": "whatsapp_business_account", "entry": [{"id": "100000000000000", "changes": [{"field": "messages", "value": {"messaging_product": "whatsapp", "metadata": {"display_phone_number": "570000000000", "phone_number_id": "100000000000001"}, "contacts": [{"profile": {"name": "Cliente Demo"}, "wa_id": "573000000000"}], "messages": [{"from": "573000000000", "id": "wamid.SIMULATED-0001", "timestamp": "1762182000", "type": "text", "text": {"body": "¿Dónde está mi guía TLX-984021-CO?"}}]}}]}]}'
```

Request:
```json
{
  "object": "whatsapp_business_account",
  "entry": [
    {
      "id": "100000000000000",
      "changes": [
        {
          "field": "messages",
          "value": {
            "messaging_product": "whatsapp",
            "metadata": {
              "display_phone_number": "570000000000",
              "phone_number_id": "100000000000001"
            },
            "contacts": [
              {
                "profile": {
                  "name": "Cliente Demo"
                },
                "wa_id": "573000000000"
              }
            ],
            "messages": [
              {
                "from": "573000000000",
                "id": "wamid.SIMULATED-0001",
                "timestamp": "1762182000",
                "type": "text",
                "text": {
                  "body": "¿Dónde está mi guía TLX-984021-CO?"
                }
              }
            ]
          }
        }
      ]
    }
  ]
}
```

Response:
```json
{
  "delivery_mode": "SIMULATED",
  "results": [
    {
      "in_reply_to": "wamid.SIMULATED-0001",
      "reply": {
        "session_id": "f94da3bb-87a8-437c-878d-273fb284a335",
        "in_reply_to": "wamid.SIMULATED-0001",
        "reply_id": "4594f341-e97e-45c3-81af-8ab2b73df718",
        "channel": "WHATSAPP",
        "status": "AWAITING_USER",
        "blocks": [
          {
            "type": "TEXT",
            "text": "Guía TLX-984021-CO: En tránsito. Tu envío va en carretera hacia la ciudad de destino. Ubicación de referencia: Paso de Montaña Alto de la Línea - Sentido Bogotá-Ibague-Medellín. Entrega estimada: 3 de noviembre de 2026, 4:00 p. m. Última temperatura reportada: 3,8 °C."
          },
          {
            "type": "TRACKING_CARD",
            "numero_guia": "TLX-984021-CO",
            "estado": {
              "codigo": "EN_TRANSITO",
              "etiqueta": "En tránsito",
              "explicacion": "Tu envío va en carretera hacia la ciudad de destino."
            },
            "eta": "2026-11-03T16:00:00-05:00",
            "ubicacion_referencia": "Paso de Montaña Alto de la Línea - Sentido Bogotá-Ibague-Medellín",
            "eventos": [
              {
                "timestamp": "2026-11-02T18:45:00-05:00",
                "estado": "EN_BODEGA_ORIGEN",
                "etiqueta": "En bodega de origen",
                "ubicacion": "Hub Central Zona Franca Fontibón",
                "descripcion_cliente": "Consolidación en camión refrigerado."
              },
              {
                "timestamp": "2026-11-03T02:15:00-05:00",
                "estado": "EN_TRANSITO",
                "etiqueta": "En tránsito",
                "ubicacion": "Peaje Chicoral - Tolima",
                "descripcion_cliente": "Monitoreo GPS activo. Temperatura de termógrafo estable a 3.8°C."
              },
              {
                "timestamp": "2026-11-03T08:30:00-05:00",
                "estado": "EN_TRANSITO",
                "etiqueta": "En tránsito",
                "ubicacion": "Corredor Calarcá - Pereira",
                "descripcion_cliente": "Vehículo en ruta regular. Arribo estimado a Hub Itagüí a las 12:30 PM."
              }
            ],
            "flags": {
              "requiere_refrigeracion": true,
              "mercancia_peligrosa": false
            }
          },
          {
            "type": "QUICK_REPLIES",
            "options": [
              {
                "id": "cotizar",
                "title": "Cotizar un envío"
              },
              {
                "id": "asesor",
                "title": "Hablar con un asesor"
              }
            ]
          }
        ],
        "meta": {
          "trace_id": "7f4753be-7479-4616-bcff-4036aa7544ec",
          "intent": "TRACKING",
          "confidence_score": null,
          "confidence_method": null,
          "escalation": {
            "escalate": false,
            "primary": null,
            "triggers": [],
            "mensaje_cliente": null,
            "rules_version": "1.0.2026",
            "config_snapshot": {
              "omitidas": "CRIT-001:sin_respuesta_rag,CRIT-003:fuera_de_contexto_de_reclamo,INFO-INTENTOS:sin_dato",
              "CRIT003_MODE": "claim_context",
              "CRIT005_MODE": "incident_context",
              "PRIORITY_ORDER": "CRIT-005>CRIT-003>CRIT-002>CRIT-004>CRIT-001>INFO-INTENTOS",
              "CONFIDENCE_THRESHOLD": "0.75"
            }
          },
          "services_called": [
            "tms",
            "escalation"
          ],
          "evidence_ids": [
            "TLX-984021-CO"
          ],
          "llm_used": false,
          "delivery_mode": "SIMULATED"
        },
        "created_at": "2026-11-03T12:24:23.430459-05:00"
      },
      "outbound": {
        "channel": "WHATSAPP",
        "session_id": "f94da3bb-87a8-437c-878d-273fb284a335",
        "reply_id": "4594f341-e97e-45c3-81af-8ab2b73df718",
        "delivery_mode": "SIMULATED",
        "parts": [
          {
            "type": "interactive",
            "interactive": {
              "type": "button",
              "body": {
                "text": "Guía TLX-984021-CO: En tránsito. Tu envío va en carretera hacia la ciudad de destino. Ubicación de referencia: Paso de Montaña Alto de la Línea - Sentido Bogotá-Ibague-Medellín. Entrega estimada: 3 de noviembre de 2026, 4:00 p. m. Última temperatura reportada: 3,8 °C."
              },
              "action": {
                "buttons": [
                  {
                    "type": "reply",
                    "reply": {
                      "id": "cotizar",
                      "title": "Cotizar un envío"
                    }
                  },
                  {
                    "type": "reply",
                    "reply": {
                      "id": "asesor",
                      "title": "Hablar con un asesor"
                    }
                  }
                ]
              }
            }
          }
        ],
        "plain_text": "Guía TLX-984021-CO: En tránsito. Tu envío va en carretera hacia la ciudad de destino. Ubicación de referencia: Paso de Montaña Alto de la Línea - Sentido Bogotá-Ibague-Medellín. Entrega estimada: 3 de noviembre de 2026, 4:00 p. m. Última temperatura reportada: 3,8 °C."
      },
      "delivery": {
        "delivery_mode": "SIMULATED",
        "status": "NOT_ATTEMPTED",
        "provider_message_id": null,
        "error_code": null
      }
    }
  ]
}
```


---

### C12. `POST /api/v1/channels/pbx/turns`

Turno de llamada (transcripción o referencia de audio). `message_id` interno = `<call_id>:<turn_index>`.

- **Módulo:** PBX · **Estado:** SIMULADO (audio_ref → 503) · **operationId:** `postPbxTurn`
- **Autenticación:** —
- **Headers:** `Content-Type: application/json`; `X-Request-ID` opcional (la respuesta siempre lo devuelve).

**Parámetros**

Sin parámetros.

**Body:** `application/json` → `PbxTurnRequest` (obligatorio).

**Respuestas**

- `200` → `PbxTurnResponse`
- `503` `NO_CONFIGURADO` → `ErrorEnvelope`
- `422` `VALIDACION` → `ErrorEnvelope` (campos inválidos)
- `500` `ERROR_INTERNO` → `ErrorEnvelope`

**Ejemplos reales**

**pbx.turns** — HTTP `200`

```bash
curl -s -X POST 'http://localhost:8000/api/v1/channels/pbx/turns' \
  -H 'Content-Type: application/json' \
  -d '{"call_id": "call-doc", "caller_id": "+573000000001", "turn_index": 1, "event": "TURN", "transcript": "T L X nueve ocho cuatro cero dos uno C O, ¿dónde está?", "audio_ref": null, "transcript_confidence": 0.86, "dnis": "6015550100", "silence_ms": 800, "barge_in": false}'
```

Request:
```json
{
  "call_id": "call-doc",
  "caller_id": "+573000000001",
  "turn_index": 1,
  "event": "TURN",
  "transcript": "T L X nueve ocho cuatro cero dos uno C O, ¿dónde está?",
  "audio_ref": null,
  "transcript_confidence": 0.86,
  "dnis": "6015550100",
  "silence_ms": 800,
  "barge_in": false
}
```

Response:
```json
{
  "reply": {
    "session_id": "4108611f-7285-40d1-b924-53335f5ad169",
    "in_reply_to": "call-doc:1",
    "reply_id": "960809ff-79db-47bd-8aaa-daab207138f9",
    "channel": "PBX",
    "status": "AWAITING_USER",
    "blocks": [
      {
        "type": "TEXT",
        "text": "Guía TLX-984021-CO: En tránsito. Tu envío va en carretera hacia la ciudad de destino. Ubicación de referencia: Paso de Montaña Alto de la Línea - Sentido Bogotá-Ibague-Medellín. Entrega estimada: 3 de noviembre de 2026, 4:00 p. m. Última temperatura reportada: 3,8 °C."
      },
      {
        "type": "TRACKING_CARD",
        "numero_guia": "TLX-984021-CO",
        "estado": {
          "codigo": "EN_TRANSITO",
          "etiqueta": "En tránsito",
          "explicacion": "Tu envío va en carretera hacia la ciudad de destino."
        },
        "eta": "2026-11-03T16:00:00-05:00",
        "ubicacion_referencia": "Paso de Montaña Alto de la Línea - Sentido Bogotá-Ibague-Medellín",
        "eventos": [
          {
            "timestamp": "2026-11-02T18:45:00-05:00",
            "estado": "EN_BODEGA_ORIGEN",
            "etiqueta": "En bodega de origen",
            "ubicacion": "Hub Central Zona Franca Fontibón",
            "descripcion_cliente": "Consolidación en camión refrigerado."
          },
          {
            "timestamp": "2026-11-03T02:15:00-05:00",
            "estado": "EN_TRANSITO",
            "etiqueta": "En tránsito",
            "ubicacion": "Peaje Chicoral - Tolima",
            "descripcion_cliente": "Monitoreo GPS activo. Temperatura de termógrafo estable a 3.8°C."
          },
          {
            "timestamp": "2026-11-03T08:30:00-05:00",
            "estado": "EN_TRANSITO",
            "etiqueta": "En tránsito",
            "ubicacion": "Corredor Calarcá - Pereira",
            "descripcion_cliente": "Vehículo en ruta regular. Arribo estimado a Hub Itagüí a las 12:30 PM."
          }
        ],
        "flags": {
          "requiere_refrigeracion": true,
          "mercancia_peligrosa": false
        }
      },
      {
        "type": "QUICK_REPLIES",
        "options": [
          {
            "id": "cotizar",
            "title": "Cotizar un envío"
          },
          {
            "id": "asesor",
            "title": "Hablar con un asesor"
          }
        ]
      }
    ],
    "meta": {
      "trace_id": "8ae4217e-92f3-4d56-ac7c-284e4d4ec01b",
      "intent": "TRACKING",
      "confidence_score": null,
      "confidence_method": null,
      "escalation": {
        "escalate": false,
        "primary": null,
        "triggers": [],
        "mensaje_cliente": null,
        "rules_version": "1.0.2026",
        "config_snapshot": {
          "CRIT003_MODE": "claim_context",
          "CRIT005_MODE": "incident_context",
          "CONFIDENCE_THRESHOLD": "0.75",
          "PRIORITY_ORDER": "CRIT-005>CRIT-003>CRIT-002>CRIT-004>CRIT-001>INFO-INTENTOS",
          "omitidas": "CRIT-001:sin_respuesta_rag,CRIT-003:fuera_de_contexto_de_reclamo,INFO-INTENTOS:sin_dato"
        }
      },
      "services_called": [
        "tms",
        "escalation"
      ],
      "evidence_ids": [
        "TLX-984021-CO"
      ],
      "llm_used": false,
      "delivery_mode": "SIMULATED"
    },
    "created_at": "2026-11-03T13:17:37.737885-05:00"
  },
  "speech_text": "Guía TLX- nueve ocho cuatro cero dos uno -CO: En tránsito. Tu envío va en carretera hacia la ciudad de destino. Ubicación de referencia: Paso de Montaña Alto de la Línea - Sentido Bogotá-Ibague-Medellín. Entrega estimada: tres de noviembre de",
  "transfer": false,
  "delivery": {
    "delivery_mode": "SIMULATED",
    "status": "NOT_ATTEMPTED",
    "provider_message_id": null,
    "error_code": null
  }
}
```

**pbx.turns_audio** — HTTP `503`

```bash
curl -s -X POST 'http://localhost:8000/api/v1/channels/pbx/turns' \
  -H 'Content-Type: application/json' \
  -d '{"call_id": "call-doc2", "caller_id": "+573000000001", "turn_index": 1, "event": "TURN", "transcript": null, "audio_ref": "local:audio-1", "transcript_confidence": 0.86, "dnis": "6015550100", "silence_ms": 800, "barge_in": false}'
```

Request:
```json
{
  "call_id": "call-doc2",
  "caller_id": "+573000000001",
  "turn_index": 1,
  "event": "TURN",
  "transcript": null,
  "audio_ref": "local:audio-1",
  "transcript_confidence": 0.86,
  "dnis": "6015550100",
  "silence_ms": 800,
  "barge_in": false
}
```

Response:
```json
{
  "error": {
    "code": "NO_CONFIGURADO",
    "message": "La transcripción de audio (ASR) no está integrada.",
    "details": {},
    "request_id": "ce374bce-c55c-49cd-b042-9ebac4a7b85f"
  }
}
```


---

### C13. `POST /api/v1/channels/pbx/simulate`

Atajo de simulación de PBX (transcripción + call_id).

- **Módulo:** PBX · **Estado:** SIMULADO · **operationId:** `simulatePbx`
- **Autenticación:** —
- **Headers:** `Content-Type: application/json`; `X-Request-ID` opcional (la respuesta siempre lo devuelve).

**Parámetros**

Sin parámetros.

**Body:** `application/json` → `PbxSimulateRequest` (obligatorio).

**Respuestas**

- `200` → `PbxTurnResponse`
- `422` `VALIDACION` → `ErrorEnvelope` (campos inválidos)
- `500` `ERROR_INTERNO` → `ErrorEnvelope`

**Ejemplos reales**

**pbx.simulate** — HTTP `200`

```bash
curl -s -X POST 'http://localhost:8000/api/v1/channels/pbx/simulate' \
  -H 'Content-Type: application/json' \
  -d '{"call_id": "call-7d1e", "transcript": "T L X nueve ocho cuatro cero dos uno C O, ¿dónde está?", "turn_index": 1}'
```

Request:
```json
{
  "call_id": "call-7d1e",
  "transcript": "T L X nueve ocho cuatro cero dos uno C O, ¿dónde está?",
  "turn_index": 1
}
```

Response:
```json
{
  "reply": {
    "session_id": "e6d764bf-db4e-45a8-b52a-e31551ece900",
    "in_reply_to": "call-7d1e:1",
    "reply_id": "8a2f1cda-0305-42fe-a375-9fe868aa3f0c",
    "channel": "PBX",
    "status": "AWAITING_USER",
    "blocks": [
      {
        "type": "TEXT",
        "text": "Guía TLX-984021-CO: En tránsito. Tu envío va en carretera hacia la ciudad de destino. Ubicación de referencia: Paso de Montaña Alto de la Línea - Sentido Bogotá-Ibague-Medellín. Entrega estimada: 3 de noviembre de 2026, 4:00 p. m. Última temperatura reportada: 3,8 °C."
      },
      {
        "type": "TRACKING_CARD",
        "numero_guia": "TLX-984021-CO",
        "estado": {
          "codigo": "EN_TRANSITO",
          "etiqueta": "En tránsito",
          "explicacion": "Tu envío va en carretera hacia la ciudad de destino."
        },
        "eta": "2026-11-03T16:00:00-05:00",
        "ubicacion_referencia": "Paso de Montaña Alto de la Línea - Sentido Bogotá-Ibague-Medellín",
        "eventos": [
          {
            "timestamp": "2026-11-02T18:45:00-05:00",
            "estado": "EN_BODEGA_ORIGEN",
            "etiqueta": "En bodega de origen",
            "ubicacion": "Hub Central Zona Franca Fontibón",
            "descripcion_cliente": "Consolidación en camión refrigerado."
          },
          {
            "timestamp": "2026-11-03T02:15:00-05:00",
            "estado": "EN_TRANSITO",
            "etiqueta": "En tránsito",
            "ubicacion": "Peaje Chicoral - Tolima",
            "descripcion_cliente": "Monitoreo GPS activo. Temperatura de termógrafo estable a 3.8°C."
          },
          {
            "timestamp": "2026-11-03T08:30:00-05:00",
            "estado": "EN_TRANSITO",
            "etiqueta": "En tránsito",
            "ubicacion": "Corredor Calarcá - Pereira",
            "descripcion_cliente": "Vehículo en ruta regular. Arribo estimado a Hub Itagüí a las 12:30 PM."
          }
        ],
        "flags": {
          "requiere_refrigeracion": true,
          "mercancia_peligrosa": false
        }
      },
      {
        "type": "QUICK_REPLIES",
        "options": [
          {
            "id": "cotizar",
            "title": "Cotizar un envío"
          },
          {
            "id": "asesor",
            "title": "Hablar con un asesor"
          }
        ]
      }
    ],
    "meta": {
      "trace_id": "acb78981-4bb5-41f8-8a90-ec74dc5a695f",
      "intent": "TRACKING",
      "confidence_score": null,
      "confidence_method": null,
      "escalation": {
        "escalate": false,
        "primary": null,
        "triggers": [],
        "mensaje_cliente": null,
        "rules_version": "1.0.2026",
        "config_snapshot": {
          "omitidas": "CRIT-001:sin_respuesta_rag,CRIT-003:fuera_de_contexto_de_reclamo,INFO-INTENTOS:sin_dato",
          "CRIT003_MODE": "claim_context",
          "CRIT005_MODE": "incident_context",
          "PRIORITY_ORDER": "CRIT-005>CRIT-003>CRIT-002>CRIT-004>CRIT-001>INFO-INTENTOS",
          "CONFIDENCE_THRESHOLD": "0.75"
        }
      },
      "services_called": [
        "tms",
        "escalation"
      ],
      "evidence_ids": [
        "TLX-984021-CO"
      ],
      "llm_used": false,
      "delivery_mode": "SIMULATED"
    },
    "created_at": "2026-11-03T12:24:25.780924-05:00"
  },
  "speech_text": "Guía TLX- nueve ocho cuatro cero dos uno -CO: En tránsito. Tu envío va en carretera hacia la ciudad de destino. Ubicación de referencia: Paso de Montaña Alto de la Línea - Sentido Bogotá-Ibague-Medellín. Entrega estimada: tres de noviembre de",
  "transfer": false,
  "delivery": {
    "delivery_mode": "SIMULATED",
    "status": "NOT_ATTEMPTED",
    "provider_message_id": null,
    "error_code": null
  }
}
```


---

### C14. `GET /api/v1/handoffs`

Bandeja mínima de asesor.

- **Módulo:** Handoff · **Estado:** PROBADO · **operationId:** `listHandoffs`
- **Autenticación:** `X-Admin-Token`
- **Headers:** ninguno obligatorio; `X-Admin-Token`; `X-Request-ID` opcional (la respuesta siempre lo devuelve).

**Parámetros**

| Parámetro | En | Obligatorio | Tipo |
|---|---|---|---|
| `status` | query | no | `HandoffStatus | null` |

**Body:** Sin cuerpo.

**Respuestas**

- `200` → `HandoffListResponse`
- `403` `NO_AUTORIZADO` → `ErrorEnvelope`
- `422` `VALIDACION` → `ErrorEnvelope` (campos inválidos)
- `500` `ERROR_INTERNO` → `ErrorEnvelope`

**Ejemplos reales**

**handoffs.list** — HTTP `200`

```bash
curl -s -X GET 'http://localhost:8000/api/v1/handoffs?status=PENDING' \
  -H "X-Admin-Token: $ADMIN_TOKEN"
```

Response:
```json
{
  "items": [
    {
      "handoff_id": "a4ef8b2d-22a2-4ab8-8232-12a9faf1c71e",
      "session_id": "fa2d460f-9ed4-47ac-9323-0f3df47ea4e7",
      "canal": "WEB_CHAT",
      "prioridad": "ALTA",
      "cola_destino": "JURIDICO_PQRS",
      "numero_guia_consultada": "TLX-752190-CO",
      "status": "PENDING",
      "created_at": "2026-11-03T13:17:37.700994-05:00"
    },
    {
      "handoff_id": "4a9cbcae-2bf4-488f-901c-dfd0eb336279",
      "session_id": "e691d3a8-9716-4f12-b57b-4eb2f11481a1",
      "canal": "WEB_CHAT",
      "prioridad": "ALTA",
      "cola_destino": "SUPERVISOR",
      "numero_guia_consultada": null,
      "status": "PENDING",
      "created_at": "2026-11-03T13:17:37.687335-05:00"
    },
    {
      "handoff_id": "6c72e632-2ce5-4664-94ec-aac9ba6ba360",
      "session_id": "8900701e-fe1c-410a-bee9-6a12d3d9f0ab",
      "canal": "WEB_CHAT",
      "prioridad": "URGENTE",
      "cola_destino": "OPERACIONES",
      "numero_guia_consultada": "TLX-310482-CO",
      "status": "PENDING",
      "created_at": "2026-11-03T13:17:37.672181-05:00"
    },
    "… (10 elementos más; ver docs/api-examples)"
  ]
}
```

**handoffs.403** — HTTP `403`

```bash
curl -s -X GET 'http://localhost:8000/api/v1/handoffs'
```

Response:
```json
{
  "error": {
    "code": "NO_AUTORIZADO",
    "message": "No tienes autorización para realizar esta operación.",
    "details": {},
    "request_id": "3aec6c6e-3389-4983-aaaf-ed34ef754a1e"
  }
}
```


---

### C15. `GET /api/v1/handoffs/{handoff_id}`

Detalle y payload de un handoff. Contiene historial de la conversación: requiere token (DEC-C13).

- **Módulo:** Handoff · **Estado:** PROBADO · **operationId:** `getHandoff`
- **Autenticación:** `X-Admin-Token`
- **Headers:** ninguno obligatorio; `X-Admin-Token`; `X-Request-ID` opcional (la respuesta siempre lo devuelve).

**Parámetros**

| Parámetro | En | Obligatorio | Tipo |
|---|---|---|---|
| `handoff_id` | path | sí | `string` |

**Body:** Sin cuerpo.

**Respuestas**

- `200` → `HandoffPayload`
- `403` `NO_AUTORIZADO` → `ErrorEnvelope`
- `404` `NO_ENCONTRADO` → `ErrorEnvelope`
- `422` `VALIDACION` → `ErrorEnvelope` (campos inválidos)
- `500` `ERROR_INTERNO` → `ErrorEnvelope`

**Ejemplos reales**

**handoffs.get** — HTTP `200`

```bash
curl -s -X GET 'http://localhost:8000/api/v1/handoffs/6c72e632-2ce5-4664-94ec-aac9ba6ba360' \
  -H "X-Admin-Token: $ADMIN_TOKEN"
```

Response:
```json
{
  "handoff_id": "6c72e632-2ce5-4664-94ec-aac9ba6ba360",
  "session_id": "8900701e-fe1c-410a-bee9-6a12d3d9f0ab",
  "canal": "WEB_CHAT",
  "numero_guia_consultada": "TLX-310482-CO",
  "confidence_score_promedio": null,
  "sentimiento_detectado": "NEUTRO",
  "intentos_previos": 0,
  "resumen_contexto": "Intención: TRACKING. Canal: WEB_CHAT. Guía: TLX-310482-CO (estado TMS RETENIDO_NOVEDAD). Motivo: CRIT-005 — mercancia_peligrosa=true; estado_guia=RETENIDO_NOVEDAD. Sentimiento: NEUTRO. Intentos previos: 0. Último mensaje del cliente: «TLX-310482-CO».",
  "historial": [
    {
      "rol": "USUARIO",
      "texto": "TLX-310482-CO",
      "timestamp": "2026-11-03T13:17:37.667457-05:00",
      "canal": "WEB_CHAT"
    }
  ],
  "prioridad": "URGENTE",
  "cola_destino": "OPERACIONES",
  "triggers": [
    {
      "id_criterio": "CRIT-005",
      "accion": "ESCALAR_URGENCIA_OPERACIONES",
      "accion_raw": "ESCALAR_Urgencia_OPERACIONES",
      "evidencia": "mercancia_peligrosa=true; estado_guia=RETENIDO_NOVEDAD",
      "modo": "incident_context"
    }
  ],
  "tiempo_espera_estimado_segundos": 45,
  "status": "PENDING",
  "created_at": "2026-11-03T13:17:37.672181-05:00"
}
```


---

### C16. `PATCH /api/v1/handoffs/{handoff_id}`

Asigna o cierra un handoff (bandeja de asesor). PENDING → ASSIGNED → CLOSED; emite `handoff_status` por WebSocket.

- **Módulo:** Handoff · **Estado:** PROBADO · **operationId:** `updateHandoffStatus`
- **Autenticación:** `X-Admin-Token`
- **Headers:** `Content-Type: application/json`; `X-Admin-Token`; `X-Request-ID` opcional (la respuesta siempre lo devuelve).

**Parámetros**

| Parámetro | En | Obligatorio | Tipo |
|---|---|---|---|
| `handoff_id` | path | sí | `string` |

**Body:** `application/json` → `HandoffStatusUpdate` (obligatorio).

**Respuestas**

- `200` → `HandoffPayload`
- `403` `NO_AUTORIZADO` → `ErrorEnvelope`
- `404` `NO_ENCONTRADO` → `ErrorEnvelope`
- `422` `VALIDACION` → `ErrorEnvelope` (observado)
- `500` `ERROR_INTERNO` → `ErrorEnvelope`

**Ejemplos reales**

**handoffs.patch_assigned** — HTTP `200`

```bash
curl -s -X PATCH 'http://localhost:8000/api/v1/handoffs/6c72e632-2ce5-4664-94ec-aac9ba6ba360' \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"status": "ASSIGNED"}'
```

Request:
```json
{
  "status": "ASSIGNED"
}
```

Response:
```json
{
  "handoff_id": "6c72e632-2ce5-4664-94ec-aac9ba6ba360",
  "session_id": "8900701e-fe1c-410a-bee9-6a12d3d9f0ab",
  "canal": "WEB_CHAT",
  "numero_guia_consultada": "TLX-310482-CO",
  "confidence_score_promedio": null,
  "sentimiento_detectado": "NEUTRO",
  "intentos_previos": 0,
  "resumen_contexto": "Intención: TRACKING. Canal: WEB_CHAT. Guía: TLX-310482-CO (estado TMS RETENIDO_NOVEDAD). Motivo: CRIT-005 — mercancia_peligrosa=true; estado_guia=RETENIDO_NOVEDAD. Sentimiento: NEUTRO. Intentos previos: 0. Último mensaje del cliente: «TLX-310482-CO».",
  "historial": [
    {
      "rol": "USUARIO",
      "texto": "TLX-310482-CO",
      "timestamp": "2026-11-03T13:17:37.667457-05:00",
      "canal": "WEB_CHAT"
    }
  ],
  "prioridad": "URGENTE",
  "cola_destino": "OPERACIONES",
  "triggers": [
    {
      "id_criterio": "CRIT-005",
      "accion": "ESCALAR_URGENCIA_OPERACIONES",
      "accion_raw": "ESCALAR_Urgencia_OPERACIONES",
      "evidencia": "mercancia_peligrosa=true; estado_guia=RETENIDO_NOVEDAD",
      "modo": "incident_context"
    }
  ],
  "tiempo_espera_estimado_segundos": 45,
  "status": "ASSIGNED",
  "created_at": "2026-11-03T13:17:37.672181-05:00"
}
```

**handoffs.patch_invalid** — HTTP `422`

```bash
curl -s -X PATCH 'http://localhost:8000/api/v1/handoffs/6c72e632-2ce5-4664-94ec-aac9ba6ba360' \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"status": "ASSIGNED"}'
```

Request:
```json
{
  "status": "ASSIGNED"
}
```

Response:
```json
{
  "error": {
    "code": "VALIDACION",
    "message": "Transición no permitida: ASSIGNED → ASSIGNED.",
    "details": {},
    "request_id": "a8020938-a887-4d41-8ac8-659921a7fa54"
  }
}
```

**handoffs.patch_closed** — HTTP `200`

```bash
curl -s -X PATCH 'http://localhost:8000/api/v1/handoffs/6c72e632-2ce5-4664-94ec-aac9ba6ba360' \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"status": "CLOSED"}'
```

Request:
```json
{
  "status": "CLOSED"
}
```

Response:
```json
{
  "handoff_id": "6c72e632-2ce5-4664-94ec-aac9ba6ba360",
  "session_id": "8900701e-fe1c-410a-bee9-6a12d3d9f0ab",
  "canal": "WEB_CHAT",
  "numero_guia_consultada": "TLX-310482-CO",
  "confidence_score_promedio": null,
  "sentimiento_detectado": "NEUTRO",
  "intentos_previos": 0,
  "resumen_contexto": "Intención: TRACKING. Canal: WEB_CHAT. Guía: TLX-310482-CO (estado TMS RETENIDO_NOVEDAD). Motivo: CRIT-005 — mercancia_peligrosa=true; estado_guia=RETENIDO_NOVEDAD. Sentimiento: NEUTRO. Intentos previos: 0. Último mensaje del cliente: «TLX-310482-CO».",
  "historial": [
    {
      "rol": "USUARIO",
      "texto": "TLX-310482-CO",
      "timestamp": "2026-11-03T13:17:37.667457-05:00",
      "canal": "WEB_CHAT"
    }
  ],
  "prioridad": "URGENTE",
  "cola_destino": "OPERACIONES",
  "triggers": [
    {
      "id_criterio": "CRIT-005",
      "accion": "ESCALAR_URGENCIA_OPERACIONES",
      "accion_raw": "ESCALAR_Urgencia_OPERACIONES",
      "evidencia": "mercancia_peligrosa=true; estado_guia=RETENIDO_NOVEDAD",
      "modo": "incident_context"
    }
  ],
  "tiempo_espera_estimado_segundos": 45,
  "status": "CLOSED",
  "created_at": "2026-11-03T13:17:37.672181-05:00"
}
```


---

### C17. `GET /api/v1/metrics/channels`

Conversaciones y mensajes por canal.

- **Módulo:** Métricas · **Estado:** PROBADO · **operationId:** `getChannelMetrics`
- **Autenticación:** —
- **Headers:** ninguno obligatorio; `X-Request-ID` opcional (la respuesta siempre lo devuelve).

**Parámetros**

| Parámetro | En | Obligatorio | Tipo |
|---|---|---|---|
| `desde` | query | no | `string | null` |
| `hasta` | query | no | `string | null` |

**Body:** Sin cuerpo.

**Respuestas**

- `200` → `ChannelMetricsResponse`
- `422` `VALIDACION` → `ErrorEnvelope` (campos inválidos)
- `500` `ERROR_INTERNO` → `ErrorEnvelope`

**Ejemplos reales**

**metrics** — HTTP `200`

```bash
curl -s -X GET 'http://localhost:8000/api/v1/metrics/channels'
```

Response:
```json
{
  "generado_en": "2026-11-03T13:17:37.725593-05:00",
  "desde": null,
  "hasta": null,
  "canales": [
    {
      "channel": "WHATSAPP",
      "conversaciones": 1,
      "mensajes_entrantes": 2,
      "mensajes_salientes": 2,
      "handoffs": 0
    },
    {
      "channel": "WEB_CHAT",
      "conversaciones": 41,
      "mensajes_entrantes": 57,
      "mensajes_salientes": 57,
      "handoffs": 14
    },
    {
      "channel": "PBX",
      "conversaciones": 2,
      "mensajes_entrantes": 2,
      "mensajes_salientes": 2,
      "handoffs": 0
    }
  ]
}
```


---

### C18. `POST /api/v1/admin/rag/reindex`

Reindexación del corpus RAG.

- **Módulo:** RAG (admin) · **Estado:** PROBADO · **operationId:** `reindexRag`
- **Autenticación:** `X-Admin-Token`
- **Headers:** `Content-Type: application/json`; `X-Admin-Token`; `X-Request-ID` opcional (la respuesta siempre lo devuelve).

**Parámetros**

Sin parámetros.

**Body:** `application/json` → `ReindexRequest | null` (opcional).

**Respuestas**

- `200` → `ReindexResponse`
- `403` `NO_AUTORIZADO` → `ErrorEnvelope`
- `503` `LLM_NO_DISPONIBLE` → `ErrorEnvelope`
- `422` `VALIDACION` → `ErrorEnvelope` (campos inválidos)
- `500` `ERROR_INTERNO` → `ErrorEnvelope`

**Ejemplos reales**

**reindex** — HTTP `200`

```bash
curl -s -X POST 'http://localhost:8000/api/v1/admin/rag/reindex' \
  -H "X-Admin-Token: $ADMIN_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"force": false}'
```

Request:
```json
{
  "force": false
}
```

Response:
```json
{
  "status": "COMPLETED",
  "documentos": 0,
  "chunks": 0,
  "omitidos_sin_cambios": 4,
  "duracion_ms": 4,
  "embed_model": "qwen3-embedding:0.6b",
  "embed_dim": 1024
}
```

**reindex.403** — HTTP `403`

```bash
curl -s -X POST 'http://localhost:8000/api/v1/admin/rag/reindex' \
  -H 'Content-Type: application/json' \
  -d '{}'
```

Request:
```json
{}
```

Response:
```json
{
  "error": {
    "code": "NO_AUTORIZADO",
    "message": "No tienes autorización para realizar esta operación.",
    "details": {},
    "request_id": "7f05ef6a-47ba-4ea4-aada-cf3e734d0e7f"
  }
}
```


---

## D. Integración del chat (Web Chat)

### D.1 Identificadores que maneja la UI

| Id | Quién lo crea | Dónde guardarlo | Reglas |
|---|---|---|---|
| `client_id` | **La UI**, una vez por navegador: `crypto.randomUUID()` | `localStorage` | UUID **v4** obligatorio. Identifica al usuario; el backend solo guarda un hash HMAC. Es la "llave" de la sesión: no lo compartas ni lo muestres |
| `message_id` | **La UI**, uno por mensaje: `crypto.randomUUID()` | En memoria, hasta recibir respuesta | UUID v4. **Idempotente**: reenviar el mismo `message_id` devuelve la misma respuesta (mismo `reply_id`) sin reprocesar. Úsalo para reintentos seguros |
| `session_id` | **El backend**, en cada `reply.session_id` | `localStorage`, junto al `client_id` | **Actualízalo con cada respuesta**: puede cambiar si la sesión expiró o se cerró |

### D.2 Crear una conversación y enviar mensajes

1. **Primer mensaje:** `POST /api/v1/chat/messages` con `client_id`, `message_id` y `text`, **sin**
   `session_id`. El backend:
   - reutiliza la última sesión abierta de ese `client_id` si tuvo actividad en los últimos 30
     minutos;
   - si no, crea una nueva.
2. Guardar `reply.session_id`.
3. **Siguientes mensajes:** enviar el mismo `client_id` y el `session_id` guardado. El contexto
   (guía consultada, datos de la cotización en curso, adjuntos declarados) vive en el servidor; la
   UI no necesita reenviar nada más.
4. **Sesión expirada** (más de 30 min sin actividad) o cerrada: si envías su `session_id`, el
   backend abre **otra sesión** sin error y devuelve el `session_id` nuevo. El contexto anterior
   no se arrastra.

**Primer mensaje (crea la sesión)** — HTTP `200`

```bash
curl -s -X POST 'http://localhost:8000/api/v1/chat/messages' \
  -H 'Content-Type: application/json' \
  -d '{"client_id": "ca85aa16-726d-41d4-b466-b539344d7e56", "message_id": "6ac9ecf4-42fa-4593-9a5a-56b7c6a0cc57", "text": "Hola"}'
```

Request:
```json
{
  "client_id": "ca85aa16-726d-41d4-b466-b539344d7e56",
  "message_id": "6ac9ecf4-42fa-4593-9a5a-56b7c6a0cc57",
  "text": "Hola"
}
```

Response:
```json
{
  "reply": {
    "session_id": "e0d4abfc-cb6a-4c30-9f56-2360d6430435",
    "in_reply_to": "6ac9ecf4-42fa-4593-9a5a-56b7c6a0cc57",
    "reply_id": "7f5a1e99-c6dc-4d4f-8851-243961f57978",
    "channel": "WEB_CHAT",
    "status": "AWAITING_USER",
    "blocks": [
      {
        "type": "TEXT",
        "text": "¡Hola! Soy el asistente de TransLogix. Puedo rastrear una guía, cotizar un envío, resolver dudas sobre mercancías especiales o ayudarte a radicar una PQRS."
      },
      {
        "type": "QUICK_REPLIES",
        "options": [
          {
            "id": "rastrear",
            "title": "Rastrear una guía"
          },
          {
            "id": "cotizar",
            "title": "Cotizar un envío"
          },
          {
            "id": "asesor",
            "title": "Hablar con un asesor"
          }
        ]
      }
    ],
    "meta": {
      "trace_id": "ee15da20-34c5-48ee-816c-f47679888426",
      "intent": "SALUDO",
      "confidence_score": null,
      "confidence_method": null,
      "escalation": {
        "escalate": false,
        "primary": null,
        "triggers": [],
        "mensaje_cliente": null,
        "rules_version": "1.0.2026",
        "config_snapshot": {
          "CRIT003_MODE": "claim_context",
          "CRIT005_MODE": "incident_context",
          "CONFIDENCE_THRESHOLD": "0.75",
          "PRIORITY_ORDER": "CRIT-005>CRIT-003>CRIT-002>CRIT-004>CRIT-001>INFO-INTENTOS",
          "omitidas": "CRIT-001:sin_respuesta_rag,CRIT-003:sin_valores,CRIT-004:sin_datos_cuenta,CRIT-005:sin_datos_de_carga,INFO-INTENTOS:sin_dato"
        }
      },
      "services_called": [
        "escalation"
      ],
      "evidence_ids": [],
      "llm_used": false,
      "delivery_mode": "SIMULATED"
    },
    "created_at": "2026-11-03T13:17:28.994065-05:00"
  }
}
```

### D.3 Renderizar la respuesta

`reply.blocks` es una lista **ordenada** de bloques, discriminada por `type`. Renderízala en
orden; hay siempre al menos uno.

| `type` | Qué mostrar | Campos clave |
|---|---|---|
| `TEXT` | Burbuja de texto. **Texto plano**, nunca como HTML. Puede contener restos de Markdown o LaTeX del manual (p. ej. `$-20^\circ\text{C}$`) | `text` |
| `QUICK_REPLIES` | Botones (máx. 3 en WhatsApp). En web, ver D.6 | `options[].id`, `options[].title` |
| `TRACKING_CARD` | Tarjeta de guía: estado, ETA (`eta` puede ser `null`), referencia, últimos eventos, `flags` (`mercancia_peligrosa`, `cadena_frio`, …) | `numero_guia`, `estado.etiqueta`, `eventos[]` |
| `QUOTE_SUMMARY` | Resumen de cotización. Mostrar que es la fórmula `provisional-v1` y las `advertencias` | `flete_total_cop`, `peso_facturable_kg`, `tiempo_entrega_horas` |
| `CITATIONS` | Fuentes del RAG (documento, sección, extracto) | `items[].doc_title`, `section_path`, `excerpt` |
| `REQUEST_ATTACHMENTS` | Checklist de documentos para la PQRS | `checklist[].codigo`, `descripcion`, `obligatorio`, `recibido` |
| `PQRS_TICKET` | Ticket radicado: id, estado y faltantes | `ticket_id`, `estado`, `faltantes[]` |
| `HANDOFF_NOTICE` | Aviso de que un asesor tomará el caso | `mensaje_cliente`, `prioridad`, `cola`, `tiempo_espera_estimado_segundos` |

`reply.meta` sirve para depuración y analítica: `intent`, `services_called`, `llm_used`,
`evidence_ids`, `escalation` y `confidence_score`. **`confidence_score` es una heurística
(`heuristic_v1`), no una probabilidad**: no la muestres como "% de certeza".

### D.4 Estados de conversación (`reply.status`) en la UI

| Estado | ¿Lo devuelve hoy el backend? | UI recomendada |
|---|---|---|
| `AWAITING_USER` | Sí: tras cualquier respuesta normal | Input habilitado |
| `ESCALATED` | Sí: tras un handoff | Banner "Un asesor tomará tu caso", con `tiempo_espera_estimado_segundos`. El input puede seguir habilitado (ver D.5) |
| `CLOSED` | Solo en PBX, al terminar la llamada | Fin de conversación; el siguiente mensaje abre otra sesión |
| `ACTIVE` | No aparece en las respuestas (estado interno) | Igual que `AWAITING_USER` |
| `IDLE` | **No**: el recordatorio por inactividad está PENDIENTE | — |

### D.5 Cómo se produce el handoff

El backend decide el escalamiento en cada turno con reglas deterministas (el LLM no decide):

| Disparador | Ejemplo | Cola y prioridad observadas |
|---|---|---|
| CRIT-005: carga peligrosa con novedad | "TLX-310482-CO" (retenida con mercancía peligrosa) | `OPERACIONES` / `URGENTE` |
| CRIT-003: reclamación de valor alto | "Me llegó la caja rota" tras consultar TLX-752190-CO | `JURIDICO_PQRS` / `ALTA` |
| CRIT-002: pide asesor, amenaza legal o sentimiento muy negativo | "Quiero hablar con un asesor", "voy a demandar…" | `SUPERVISOR` / `ALTA` |
| CRIT-001: el RAG no tiene evidencia suficiente (confianza < 0,75) | "¿Cuánto cuesta un contenedor a Hong Kong?" | `SERVICIO_CLIENTE` / `NORMAL`; escala **sin dar respuesta** |

Cuando escala, la respuesta trae `status: "ESCALATED"` y un bloque `HANDOFF_NOTICE` con su
`handoff_id`. **Mientras el handoff esté `PENDING` o `ASSIGNED`, cualquier mensaje del cliente
recibe siempre** "Tu caso ya está con un asesor de TransLogix. Te responderá en este mismo chat."
Cuando la bandeja lo pasa a `CLOSED`, el siguiente mensaje vuelve a atenderlo el bot.

**Mensaje del cliente mientras espera al asesor** — HTTP `200`

```bash
curl -s -X POST 'http://localhost:8000/api/v1/chat/messages' \
  -H 'Content-Type: application/json' \
  -d '{"client_id": "e9cf84b3-34ff-4b52-92cd-fdbfb8ff493a", "session_id": "8900701e-fe1c-410a-bee9-6a12d3d9f0ab", "message_id": "681b467f-8537-4e95-8b0f-8f56d25c3ef6", "text": "¿Sigo esperando?"}'
```

Request:
```json
{
  "client_id": "e9cf84b3-34ff-4b52-92cd-fdbfb8ff493a",
  "session_id": "8900701e-fe1c-410a-bee9-6a12d3d9f0ab",
  "message_id": "681b467f-8537-4e95-8b0f-8f56d25c3ef6",
  "text": "¿Sigo esperando?"
}
```

Response:
```json
{
  "reply": {
    "session_id": "8900701e-fe1c-410a-bee9-6a12d3d9f0ab",
    "in_reply_to": "681b467f-8537-4e95-8b0f-8f56d25c3ef6",
    "reply_id": "494bc388-56b6-4813-a02b-e1ef513964c2",
    "channel": "WEB_CHAT",
    "status": "ESCALATED",
    "blocks": [
      {
        "type": "TEXT",
        "text": "Tu caso ya está con un asesor de TransLogix. Te responderá en este mismo chat."
      }
    ],
    "meta": {
      "trace_id": "7a3c117b-8557-4528-9196-d799f65c6213",
      "intent": "TRACKING",
      "confidence_score": null,
      "confidence_method": null,
      "escalation": null,
      "services_called": [
        "escalation"
      ],
      "evidence_ids": [
        "6c72e632-2ce5-4664-94ec-aac9ba6ba360"
      ],
      "llm_used": false,
      "delivery_mode": "SIMULATED"
    },
    "created_at": "2026-11-03T13:17:37.676802-05:00"
  }
}
```

> **Limitación bloqueante para un chat en vivo con asesor:** no existe endpoint ni WebSocket para
> que el asesor escriba al cliente, ni para que la UI reciba mensajes sin enviar uno (no hay push).
> El texto "Te responderá en este mismo chat" promete algo que el backend **aún no hace**.
> Hoy la UI puede mostrar el aviso y la bandeja del asesor puede asignar o cerrar; la conversación
> humana debe ocurrir por otro canal, o hay que implementarla (PENDIENTE).

### D.6 Quick replies en Web Chat

`ChatMessageRequest` **no tiene** campo `interactive_reply_id` (WhatsApp sí lo tiene). En web, el
botón se envía como **texto** (`text = option.title`). Probado con cada título que emite el
backend:

| `option.id` | `title` enviado como texto | Resultado real |
|---|---|---|
| `rastrear` | Rastrear una guía | OK: pide el número de guía |
| `cotizar` | Cotizar un envío | OK: inicia la cotización |
| `otra_ruta` | Cotizar otra ruta | OK: inicia la cotización |
| `asesor` | Hablar con un asesor | OK: handoff (CRIT-002) |
| `equipo_legal` | Contactar al equipo legal | Funciona **solo vía LLM** (handoff); sin Ollama no |
| `novedad` | Reportar una novedad | **Falla**: `DESCONOCIDA` ("No estoy seguro de haber entendido") |
| `otra_guia` | Escribir otra guía | **Falla**: `DESCONOCIDA`. Alternativa: que la UI pida la guía y envíe el número |
| `cadena_frio` | Agregar cadena de frío | **Falla**: responde una consulta RAG sobre cadena de frío en vez de recotizar |

Recomendación para la UI mientras no se corrija en backend:

- `novedad` → enviar el texto "Quiero reportar una novedad de mi envío".
- `otra_guia` → enfocar el input con el placeholder `TLX-123456-CO`.
- `cadena_frio` → reenviar la cotización con "cadena de frío" en el texto.

### D.7 Historial

`GET /api/v1/chat/sessions/{session_id}/history` con el header `X-Client-Id: <client_id>`.
Devuelve los mensajes en orden: los `INBOUND` traen `text`; los `OUTBOUND` traen `text` (los
TEXT concatenados) y `blocks`. Úsalo para rehidratar el chat al recargar la página.

### D.8 Sesiones inexistentes o ajenas

| Caso | Respuesta | Qué hacer en la UI |
|---|---|---|
| `session_id` que no existe, de otro `client_id` o de otro canal (POST de chat) | **404** `NO_ENCONTRADO` (no distingue los casos, a propósito) | Borrar el `session_id` guardado y reenviar el **mismo** `message_id` sin `session_id`. Es seguro: el mensaje no se guardó |
| Historial con `X-Client-Id` que no es el dueño | **404** `NO_ENCONTRADO` | Borrar `session_id` y empezar de cero |
| Mismo `message_id` reenviado | **200**, la misma respuesta | Nada: es el reintento idempotente |
| Reenvío concurrente mientras el original se procesa | **422** `VALIDACION` "Mensaje duplicado en proceso" | Reintentar con el mismo `message_id` en 2–3 s |

---

## E. Integración por módulo

### Tracking: `GET /api/v1/tracking/{guia}`
- Acepta formas normalizables (`tlx984021co`, `tlx 984021 co`); la respuesta trae `numero_guia`
  canónico.
- La vista es **segura para el cliente**: sin nombres de receptor (solo iniciales en la prueba de
  entrega), sin placas ni teléfonos. Durante el reparto solo se muestra la ciudad.
- Guías de demostración del dataset **simulado**:

  | Guía | Estado |
  |---|---|
  | TLX-984021-CO | en tránsito, cadena de frío con temperatura |
  | TLX-551904-CO | en reparto |
  | TLX-310482-CO | retenida, mercancía peligrosa |
  | TLX-752190-CO | entregada, con prueba de entrega |

- Errores: 422 `GUIA_FORMATO_INVALIDO` (validar en la UI con `^TLX-\d{6}-CO$` tras normalizar) y
  404 `GUIA_NO_ENCONTRADA`.

### Cotizaciones: `POST /api/v1/cotizar`
- Campos obligatorios: `origen`, `destino`, `alto_cm`, `ancho_cm`, `largo_cm`, `peso_real_kg` y
  `valor_declarado_cop` (**entero**; un string da 422). `requiere_cadena_frio` y
  `mercancia_peligrosa` son opcionales.
- Ciudades: nombres del directorio (`Bogotá D.C.`, `Medellín`, …), con o sin tildes, y
  `Bogotá`/`Bogota`.
- Mostrar `calculo.flete_total_cop`, el desglose (`flete_base_cop`, `seguro_cop`, `recargos[]`),
  `advertencias[]` y que la fórmula es **`provisional-v1`**: el PDF no define tarifas base, así que
  no se presentan como tarifa oficial.
- Errores 422: `RUTA_SIN_TARIFA` (no hay tarifa, no se inventa), `CIUDAD_NO_RECONOCIDA`,
  `SERVICIO_NO_DISPONIBLE` (p. ej. cadena de frío hacia un destino sin refrigeración). Mostrar el
  `message` y ofrecer asesor.

### RAG (preguntas documentales)
- **No hay endpoint RAG directo**: se usa a través del chat. Ejemplos: "¿Puedo enviar baterías de
  litio?", "¿Qué documentos necesito para una reclamación?".
- Si hay evidencia, la respuesta trae `TEXT` + `CITATIONS` y la UI debe mostrar las fuentes. Si no
  hay evidencia suficiente, **no se da respuesta** y el caso escala (CRIT-001).
- Latencia típica: 2–3 s (hasta ~6 s en la primera llamada tras arrancar). Mostrar un indicador de
  "escribiendo…".
- Reindex: `POST /api/v1/admin/rag/reindex`, solo para administración.

### Handoffs (bandeja del asesor): requiere `X-Admin-Token`
- `GET /api/v1/handoffs?status=PENDING|ASSIGNED|CLOSED`: lista (`items[]` de `HandoffSummary`).
- `GET /api/v1/handoffs/{id}`: detalle con `resumen_contexto` e `historial` (sin PII), `triggers`,
  `prioridad` y `cola_destino`.
- `PATCH /api/v1/handoffs/{id}` con `{"status":"ASSIGNED"}` o `{"status":"CLOSED"}`. Transiciones
  válidas: `PENDING→ASSIGNED`, `PENDING→CLOSED`, `ASSIGNED→CLOSED`; cualquier otra da 422.
- Sin push: la bandeja debe **consultar periódicamente** (sugerido: cada 10–15 s).

### PQRS: `POST /api/v1/pqrs/crear-ticket` (también se crean desde el chat)
- `tipo`: `PETICION | QUEJA | RECLAMO | SINIESTRO`. Un `SINIESTRO` exige
  `numero_guia` y `tipo_novedad`; `valor_reclamado_cop` es opcional.
- La respuesta trae `elegibilidad` (plazo y `fecha_limite`), `checklist` con `recibido`,
  `faltantes`, `liquidacion_preliminar` (**no vinculante**), `estado` y `escalamiento`.
- Estados: `RADICADO`, `BORRADOR` (faltan documentos), `FUERA_DE_PLAZO`, `ESCALADO`.
- Comportamiento real que debe conocer la UI: con `CRIT003_MODE=claim_context`, **cualquier**
  ticket (incluso una `PETICION`) sobre una guía con valor declarado > $5.000.000 queda `ESCALADO`
  a `JURIDICO_PQRS`. Ejemplo observado: la petición de copia de la POD de TLX-752190-CO.
- Adjuntar documentos: subirlos primero con `/attachments` y pasar
  `documentos: [{codigo, attachment_id}]`. Un `attachment_id` inexistente da 404 `NO_ENCONTRADO`.
- No hay endpoint para consultar ni actualizar un ticket después de creado (PENDIENTE).

### Adjuntos: `POST /api/v1/attachments` (multipart)
- Campos: `file` (obligatorio), `declared_doc_code` (opcional, un `DocumentoCodigo`) y
  `session_id` (opcional).
- Solo PDF o JPEG, **detectado por contenido** (la extensión no cuenta), máximo 10 MB. Validar en la
  UI antes de subir.
- En el chat: enviar `attachments: [{attachment_id, declared_doc_code}]` en `ChatMessageRequest`.
  Los que traen `declared_doc_code` se guardan en la sesión y se agregan al ticket PQRS que se cree
  después en ese chat. (Implementado; **sin prueba E2E** de un mensaje con solo adjuntos.)

### Métricas: `GET /api/v1/metrics/channels?desde=&hasta=`
- Una fila por canal (`WHATSAPP`, `WEB_CHAT`, `PBX`), incluso en cero. `desde` y `hasta` son
  ISO-8601 con offset y opcionales. **Sin autenticación.**

### Canales simulados
- **WhatsApp:** `POST /api/v1/channels/whatsapp/simulate` recibe el mismo JSON que Meta y devuelve
  `results[]` con `reply`, `outbound` (cuerpo que se enviaría a Meta, con ≤ 3 botones) y
  `delivery.status: "NOT_ATTEMPTED"`. **Nada sale a WhatsApp.** El webhook real exige credenciales:
  sin ellas responde 503.
- **PBX:** `POST /api/v1/channels/pbx/simulate` o `/turns` con una **transcripción**; devuelve
  `reply`, `speech_text` (sin dígitos, para TTS) y `transfer`. La guía dictada ("T L X nueve ocho
  cuatro…") se reconoce. No hay audio ni ASR: `audio_ref` → 503 `NO_CONFIGURADO`.
- Útil para una **demo omnicanal en la UI**: los tres canales usan el mismo núcleo y comparten las
  métricas.

---

## F. Tipos TypeScript

Generados del OpenAPI de la app real (los modelos Pydantic que usan los endpoints) más
`ErrorEnvelope` del código. Campo opcional (`?`) = no figura en `required`. Las restricciones van
como comentario.

`contracts/types/api.d.ts` es la versión generada con `openapi-typescript` del **contrato**; difiere
de la app real en el header `X-Client-Id` del historial.

```ts
/** Attachment already stored by the platform, referenced by id. */
export interface AttachmentRef {
  attachment_id: string; // minLength=1, maxLength=64
  declared_doc_code?: DocumentoCodigo | null;
}

export interface AttachmentUploadResponse {
  attachment_id: string; // Id aleatorio. — minLength=1, maxLength=64
  kind: "IMAGE" | "DOCUMENT";
  mime_type: "application/pdf" | "image/jpeg"; // Tipo detectado por contenido.
  size_bytes: number; // exclusiveMinimum=0.0
  declared_doc_code: DocumentoCodigo | null;
  sha256: string; // pattern=^[0-9a-f]{64}$
}

export interface CalculoFlete {
  peso_volumetrico_kg: number; // minimum=0.0
  peso_facturable_kg: number; // exclusiveMinimum=0.0
  tarifa_aplicada: TarifaAplicada;
  flete_minimo_cop: number; // Piso de la ruta (`flete_minimo_cop` del CSV). — minimum=0.0
  flete_minimo_aplicado: boolean; // True si peso facturable x tarifa quedó por debajo del mínimo (DEC-Q2).
  flete_base_cop: number; // máx(flete mínimo, peso facturable x tarifa). — minimum=0.0
  seguro_cop: number; // Pesos colombianos (COP), entero. — minimum=0.0
  recargos: Recargo[];
  flete_total_cop: number; // Pesos colombianos (COP), entero. — minimum=0.0
}

export interface ChannelCounters {
  channel: ChannelType;
  conversaciones: number; // minimum=0.0
  mensajes_entrantes: number; // minimum=0.0
  mensajes_salientes: number; // minimum=0.0
  handoffs: number; // minimum=0.0
}

export interface ChannelMetricsResponse {
  generado_en: string; // format=date-time
  desde: string | null; // format=date-time
  hasta: string | null; // format=date-time
  canales: ChannelCounters[]; // Una fila por canal, incluso en cero.
}

/** What ``render_outbound`` produces: ready for ``deliver``, no network involved. */
export interface ChannelOutbound {
  channel: ChannelType;
  session_id: string;
  reply_id: string;
  delivery_mode: DeliveryMode;
  parts: Record<string, unknown>[]; // Cuerpos con la forma del proveedor, en orden de envío.
  plain_text: string; // Versión en texto plano (PBX: texto hablable).
}

export interface ChannelTurnResult {
  in_reply_to: string;
  reply: ConversationReply;
  outbound: ChannelOutbound;
  delivery: DeliveryResult;
}

export type ChannelType = "WHATSAPP" | "WEB_CHAT" | "PBX";

export interface ChatMessageRequest {
  session_id?: string | null; // format=uuid4
  client_id: string; // UUID estable del navegador (external_user_id). — format=uuid4
  message_id: string; // Idempotencia: reenviar el mismo id no reprocesa. — format=uuid4
  text?: string | null; // maxLength=4096
  attachments?: AttachmentRef[];
  locale?: string;
}

export interface ChatMessageResponse {
  reply: ConversationReply;
}

export interface ChecklistItem {
  codigo: DocumentoCodigo;
  obligatorio: boolean;
  recibido: boolean;
}

export interface ChecklistItemDetallado {
  codigo: DocumentoCodigo;
  obligatorio: boolean;
  recibido: boolean;
  descripcion: string;
}

export interface CitationItem {
  doc_code: DocCode;
  doc_title: string;
  section_path: string;
  chunk_id: string;
  excerpt: string;
}

export interface CitationsBlock {
  type: "CITATIONS";
  items: CitationItem[];
}

export type ColaDestino = "SERVICIO_CLIENTE" | "SUPERVISOR" | "JURIDICO_PQRS" | "EJECUTIVO_CUENTA" | "OPERACIONES";

export interface ConversationReply {
  session_id: string; // format=uuid4
  in_reply_to: string; // message_id del InboundMessage.
  reply_id: string; // format=uuid4
  channel: ChannelType;
  status: ConversationStatus;
  blocks: (TextBlock | QuickRepliesBlock | TrackingCardBlock | QuoteSummaryBlock | CitationsBlock | RequestAttachmentsBlock | PqrsTicketBlock | HandoffNoticeBlock)[];
  meta: ReplyMeta;
  created_at: string; // format=date-time
}

export type ConversationStatus = "ACTIVE" | "AWAITING_USER" | "IDLE" | "ESCALATED" | "CLOSED";

export interface CotizacionRequest {
  origen: string; // minLength=1, maxLength=80
  destino: string; // minLength=1, maxLength=80
  alto_cm: number; // exclusiveMinimum=0.0
  ancho_cm: number; // exclusiveMinimum=0.0
  largo_cm: number; // exclusiveMinimum=0.0
  peso_real_kg: number; // exclusiveMinimum=0.0
  valor_declarado_cop: number; // Pesos colombianos (COP), entero. — minimum=0.0
  requiere_cadena_frio?: boolean; // Extensión del MVP.
  mercancia_peligrosa?: boolean; // Extensión del MVP.
}

export interface CotizacionResponse {
  cotizacion_id: string; // format=uuid4
  ruta: RutaCotizada;
  calculo: CalculoFlete;
  tiempo_entrega_horas: number; // exclusiveMinimum=0.0
  advertencias: string[];
  supuestos: string[];
  formula_version: string;
}

export type DeliveryMode = "REAL" | "SIMULATED";

export interface DeliveryResult {
  delivery_mode: DeliveryMode;
  status: "SENT" | "QUEUED" | "FAILED" | "NOT_ATTEMPTED";
  provider_message_id: string | null;
  error_code: string | null;
}

export type DocCode = "DOC-OPS-2026-03" | "DOC-PQRS-2026-04" | "COBERTURA" | "TARIFAS" | "REGLAS_ESCALACION";

export interface DocumentoAdjunto {
  codigo: DocumentoCodigo;
  attachment_id: string; // minLength=1, maxLength=64
}

/** Claim checklist codes (DEC-P4). */
export type DocumentoCodigo = "GUIA" | "FACTURA_COMERCIAL" | "CARTA_RECLAMACION" | "CERTIFICACION_BANCARIA" | "FOTOS_EMPAQUE" | "FOTOS_DANO" | "CEDULA_O_RUT" | "ACTA_CON_SALVEDAD";

export interface Elegibilidad {
  dentro_de_plazo: boolean;
  regla_aplicada: string; // pattern=^[A-Z0-9_]+$
  fecha_limite: string | null; // format=date
  nota: string | null;
  fundamento: FundamentoNormativo | null;
}

export interface ErrorBody {
  code: ErrorCode;
  message: string; // Mensaje en español para el usuario final. — minLength=1
  details?: Record<string, unknown>;
  request_id: string; // pattern=^[A-Za-z0-9._-]{1,128}$
}

export type ErrorCode = "VALIDACION" | "GUIA_FORMATO_INVALIDO" | "GUIA_NO_ENCONTRADA" | "RUTA_SIN_TARIFA" | "CIUDAD_NO_RECONOCIDA" | "SERVICIO_NO_DISPONIBLE" | "EXCEDE_LIMITE_COBERTURA" | "LLM_NO_DISPONIBLE" | "RAG_SIN_EVIDENCIA" | "FIRMA_INVALIDA" | "LIMITE_DE_TASA" | "NO_ENCONTRADO" | "ERROR_INTERNO" | "NO_CONFIGURADO" | "NO_AUTORIZADO";

/** The single error shape returned by every endpoint. */
export interface ErrorEnvelope {
  error: ErrorBody;
}

export type EscalationAction = "ESCALAR_A_HUMANO" | "ESCALAR_PRIORIDAD_ALTA" | "ESCALAR_A_DEPARTAMENTO_JURIDICO_Y_PQRS" | "ESCALAR_A_EJECUTIVO_CUENTA_DEDICADO" | "ESCALAR_URGENCIA_OPERACIONES" | "OFRECER_HANDOFF";

export interface EscalationDecision {
  escalate: boolean;
  primary: EscalationTrigger | null;
  triggers: EscalationTrigger[];
  mensaje_cliente: string | null; // Texto del JSON para el criterio primario.
  rules_version: string;
  config_snapshot: Record<string, string>;
}

export interface EscalationTrigger {
  id_criterio: "CRIT-001" | "CRIT-002" | "CRIT-003" | "CRIT-004" | "CRIT-005" | "INFO-INTENTOS";
  accion: EscalationAction;
  accion_raw: string; // Acción tal como aparece en el JSON de reglas.
  evidencia: string; // Qué dato disparó la regla, sin PII. — minLength=1, maxLength=1000
  modo: string;
}

export type EstadoTicket = "BORRADOR" | "RADICADO" | "FUERA_DE_PLAZO" | "ESCALADO";

export interface EstadoVista {
  codigo: string; // pattern=^[A-Z][A-Z0-9_]*$
  etiqueta: string; // Texto visible al cliente; no puede contener datos personales (DEC-S1). — minLength=1, maxLength=1000
  explicacion: string; // Texto visible al cliente; no puede contener datos personales (DEC-S1). — minLength=1, maxLength=1000
}

export interface EventoVista {
  timestamp: string; // format=date-time
  estado: string; // pattern=^[A-Z][A-Z0-9_]*$
  etiqueta: string; // Texto visible al cliente; no puede contener datos personales (DEC-S1). — minLength=1, maxLength=1000
  ubicacion: string | null; // minLength=1, maxLength=1000
  descripcion_cliente: string; // Texto visible al cliente; no puede contener datos personales (DEC-S1). — minLength=1, maxLength=1000
}

export interface FuenteConsulta {
  sistema: "TMS_SIMULADO" | "TMS";
  consultado_en: string; // format=date-time
}

/** Where the applied rule comes from (DEC-P2: cite, never add rights of our own). */
export interface FundamentoNormativo {
  doc_code: DocCode;
  section_path: string; // minLength=1
}

export interface HandoffListResponse {
  items: HandoffSummary[];
}

export interface HandoffNoticeBlock {
  type: "HANDOFF_NOTICE";
  handoff_id: string; // format=uuid4
  prioridad: Prioridad;
  cola: ColaDestino;
  mensaje_cliente: string;
  tiempo_espera_estimado_segundos: number; // minimum=0.0
}

/** ``flujo_transferencia_pwa`` of the rules JSON plus routing extensions. */
export interface HandoffPayload {
  handoff_id: string; // format=uuid4
  session_id: string; // format=uuid4
  canal: ChannelType;
  numero_guia_consultada: string | null; // pattern=^TLX-\d{6}-CO$
  confidence_score_promedio: number | null; // minimum=0.0, maximum=1.0
  sentimiento_detectado: Sentiment;
  intentos_previos: number; // = ConversationState.fallback_count. — minimum=0.0
  resumen_contexto: string; // Generado por IA, con revisión determinística de campos. — minLength=1
  historial: HistorialItem[];
  prioridad: Prioridad;
  cola_destino: ColaDestino;
  triggers: EscalationTrigger[];
  tiempo_espera_estimado_segundos: number; // Configurable (HANDOFF_WAIT_SECONDS); valor del JSON, no es un SLA. — minimum=0.0
  status: HandoffStatus;
  created_at: string; // format=date-time
}

export type HandoffStatus = "PENDING" | "ASSIGNED" | "CLOSED";

/** Advisor inbox action (``PATCH /api/v1/handoffs/{id}``, P1): PENDING → ASSIGNED → CLOSED. */
export interface HandoffStatusUpdate {
  status: "ASSIGNED" | "CLOSED";
}

/** Row of the advisor inbox (``GET /api/v1/handoffs``). */
export interface HandoffSummary {
  handoff_id: string; // format=uuid4
  session_id: string; // format=uuid4
  canal: ChannelType;
  prioridad: Prioridad;
  cola_destino: ColaDestino;
  numero_guia_consultada: string | null; // pattern=^TLX-\d{6}-CO$
  status: HandoffStatus;
  created_at: string; // format=date-time
}

export interface HealthResponse {
  status: "ok";
}

export interface HistorialItem {
  rol: "USUARIO" | "ASISTENTE" | "ASESOR" | "SISTEMA";
  texto: string;
  timestamp: string; // format=date-time
  canal: ChannelType;
}

export interface HistoryEntry {
  message_id: string;
  direction: MessageDirection;
  message_type: MessageType | null; // Solo mensajes entrantes.
  text: string | null;
  blocks: (TextBlock | QuickRepliesBlock | TrackingCardBlock | QuoteSummaryBlock | CitationsBlock | RequestAttachmentsBlock | PqrsTicketBlock | HandoffNoticeBlock)[] | null; // Solo respuestas (OUTBOUND).
  delivery_mode: DeliveryMode;
  created_at: string; // format=date-time
}

export type Intent = "TRACKING" | "COTIZACION" | "CONSULTA_DOCUMENTAL" | "CARGA_ESPECIAL" | "PQRS" | "SALUDO" | "HABLAR_CON_HUMANO" | "DESCONOCIDA";

export interface LiquidacionPreliminar {
  base_cop: number; // Pesos colombianos (COP), entero. — minimum=0.0
  deducible_pct: number; // minimum=0.0, maximum=100.0
  deducible_cop: number; // Pesos colombianos (COP), entero. — minimum=0.0
  flete_devuelto: "PENDIENTE_REVISION_HUMANA";
  estado: "PRELIMINAR_NO_VINCULANTE";
}

export type MessageDirection = "INBOUND" | "OUTBOUND";

export type MessageType = "TEXT" | "AUDIO_TRANSCRIPT" | "IMAGE" | "DOCUMENT" | "INTERACTIVE_REPLY" | "SYSTEM_EVENT";

export interface PbxSimulateRequest {
  call_id: string; // minLength=1, maxLength=128
  transcript: string; // minLength=1, maxLength=2000
  turn_index?: number; // minimum=0.0
  caller_id?: string; // maxLength=32
}

/** One call turn. ``message_id`` is derived as ``<call_id>:<turn_index>`` (DEC-C4). */
export interface PbxTurnRequest {
  call_id: string; // minLength=1, maxLength=128
  caller_id: string; // ANI en claro; se hashea. — minLength=1, maxLength=32
  turn_index: number; // minimum=0.0
  event?: "CALL_STARTED" | "TURN" | "CALL_ENDED";
  transcript?: string | null; // maxLength=2000
  audio_ref?: string | null;
  transcript_confidence?: number | null; // minimum=0.0, maximum=1.0
  dnis?: string | null;
  silence_ms?: number | null; // minimum=0.0
  barge_in?: boolean;
}

export interface PbxTurnResponse {
  reply: ConversationReply;
  speech_text: string; // Texto hablable, sin Markdown, emojis ni URLs; ≤ ~40 palabras.
  transfer: boolean; // True si el turno termina en transferencia a asesor.
  delivery: DeliveryResult;
}

export interface PqrsTicketBlock {
  type: "PQRS_TICKET";
  ticket_id: string; // format=uuid4
  estado: EstadoTicket;
  faltantes: DocumentoCodigo[];
}

export interface PqrsTicketRequest {
  session_id?: string | null; // format=uuid4
  tipo: PqrsTipo;
  numero_guia?: string | null; // pattern=^TLX-\d{6}-CO$
  tipo_novedad?: TipoNovedad | null;
  descripcion: string; // minLength=1, maxLength=4000
  valor_reclamado_cop?: number | null; // minimum=0.0
  documentos?: DocumentoAdjunto[];
}

export interface PqrsTicketResponse {
  ticket_id: string; // format=uuid4
  estado: EstadoTicket;
  elegibilidad: Elegibilidad | null;
  checklist: ChecklistItem[];
  faltantes: DocumentoCodigo[];
  liquidacion_preliminar: LiquidacionPreliminar | null;
  escalamiento: EscalationDecision | null;
}

export type PqrsTipo = "PETICION" | "QUEJA" | "RECLAMO" | "SINIESTRO";

export type Prioridad = "NORMAL" | "ALTA" | "URGENTE";

export interface PruebaEntregaVista {
  recibido_por_iniciales: string; // Solo iniciales; nunca nombre completo, cédula ni URL de firma. — pattern=^(?:[A-ZÁÉÍÓÚÑ]\. ?){1,4}$
  fecha_hora_entrega: string; // format=date-time
  observaciones: string | null; // minLength=1, maxLength=1000
}

export interface QuickRepliesBlock {
  type: "QUICK_REPLIES";
  options: QuickReplyOption[];
}

export interface QuickReplyOption {
  id: string; // minLength=1, maxLength=64
  title: string; // minLength=1, maxLength=80
}

export interface QuoteSummaryBlock {
  type: "QUOTE_SUMMARY";
  cotizacion_id: string; // format=uuid4
  codigo_trayecto: string;
  origen: string;
  destino: string;
  peso_facturable_kg: number; // exclusiveMinimum=0.0
  flete_total_cop: number; // Pesos colombianos (COP), entero. — minimum=0.0
  tiempo_entrega_horas: number; // exclusiveMinimum=0.0
  formula_version: string;
  advertencias: string[];
}

export interface ReadinessChecks {
  database: "ok" | "fail";
  ollama: "ok" | "fail" | "disabled";
  rag_index: "ok" | "empty" | "fail";
}

export interface ReadinessResponse {
  status: "ready" | "not_ready";
  checks: ReadinessChecks;
}

export interface Recargo {
  codigo: RecargoCodigo;
  descripcion: string;
  valor_cop: number; // Pesos colombianos (COP), entero. — minimum=0.0
}

export type RecargoCodigo = "CADENA_FRIO" | "MERCANCIA_PELIGROSA";

export interface ReindexRequest {
  doc_codes?: DocCode[] | null; // null = todos.
  force?: boolean; // Reindexar aunque el contenido no cambie.
}

export interface ReindexResponse {
  status: "COMPLETED";
  documentos: number; // minimum=0.0
  chunks: number; // minimum=0.0
  omitidos_sin_cambios: number; // minimum=0.0
  duracion_ms: number; // minimum=0.0
  embed_model: string;
  embed_dim: number; // exclusiveMinimum=0.0
}

export interface ReplyMeta {
  trace_id: string; // minLength=1
  intent: Intent;
  confidence_score: number | null; // Solo RAG; null en tracking/cotización. — minimum=0.0, maximum=1.0
  confidence_method: "heuristic_v1" | null;
  escalation: EscalationDecision | null;
  services_called: ServiceName[];
  evidence_ids: string[]; // chunk_id / guía / cotizacion_id usados.
  llm_used: boolean;
  delivery_mode: DeliveryMode;
}

export interface RequestAttachmentsBlock {
  type: "REQUEST_ATTACHMENTS";
  checklist: ChecklistItemDetallado[];
}

export interface RutaCotizada {
  codigo_trayecto: string; // pattern=^TLX-[A-Z]{3}-[A-Z]{3}-\d{3}$
  origen: string;
  destino: string;
}

export type Sentiment = "POSITIVO" | "NEUTRO" | "NEGATIVO" | "MUY_NEGATIVO";

export type ServiceName = "tms" | "quotation" | "rag" | "escalation" | "pqrs";

export interface SessionHistory {
  session_id: string; // format=uuid4
  channel: ChannelType;
  status: ConversationStatus;
  messages: HistoryEntry[]; // En orden cronológico.
}

export interface TarifaAplicada {
  tipo: "REAL" | "VOLUMETRICO";
  cop_por_kg: number; // Pesos colombianos (COP), entero. — minimum=0.0
}

export interface TextBlock {
  type: "TEXT";
  text: string; // minLength=1
}

export type TipoNovedad = "AVERIA_APARENTE" | "AVERIA_NO_APARENTE" | "PERDIDA_TOTAL" | "SAQUEO" | "OTRO";

export interface TrackingCardBlock {
  type: "TRACKING_CARD";
  numero_guia: string; // pattern=^TLX-\d{6}-CO$
  estado: EstadoVista;
  eta: string | null; // format=date-time
  ubicacion_referencia: string | null; // minLength=1, maxLength=1000
  eventos: EventoVista[];
  flags: TrackingFlags;
}

export interface TrackingFlags {
  requiere_refrigeracion: boolean;
  mercancia_peligrosa: boolean;
}

export interface TrackingResponse {
  numero_guia: string; // pattern=^TLX-\d{6}-CO$
  estado_actual: EstadoVista;
  origen_ciudad: string;
  destino_ciudad: string;
  tipo_servicio: string;
  fecha_despacho: string | null; // format=date-time
  fecha_estimada_entrega: string | null; // format=date-time
  ubicacion_referencia: string | null; // minLength=1, maxLength=1000
  eventos: EventoVista[]; // En orden cronológico.
  prueba_entrega: PruebaEntregaVista | null;
  flags: TrackingFlags;
  ultima_temperatura_c: number | null; // Solo si el TMS la reporta en los eventos.
  fuente: FuenteConsulta;
}

export interface WaChange {
  field: string;
  value: WaValue;
}

export interface WaContact {
  profile?: WaProfile | null;
  wa_id: string;
}

export interface WaEntry {
  id: string;
  changes: WaChange[];
}

export interface WaInteractive {
  type: string;
  button_reply?: WaInteractiveReply | null;
  list_reply?: WaInteractiveReply | null;
}

export interface WaInteractiveReply {
  id: string;
  title: string;
  description?: string | null;
}

export interface WaMedia {
  id: string;
  mime_type?: string | null;
  sha256?: string | null;
  caption?: string | null;
  filename?: string | null;
}

export interface WaMessage {
  from: string; // wa_id del cliente (PII).
  id: string; // wamid: clave de idempotencia con el canal.
  timestamp: string; // Segundos Unix, como string.
  type: string;
  text?: WaText | null;
  interactive?: WaInteractive | null;
  image?: WaMedia | null;
  document?: WaMedia | null;
  audio?: WaMedia | null;
}

export interface WaMetadata {
  display_phone_number: string;
  phone_number_id: string;
}

export interface WaProfile {
  name?: string | null; // PII: no se registra en logs.
}

export interface WaText {
  body: string;
}

export interface WaValue {
  messaging_product: "whatsapp";
  metadata: WaMetadata;
  contacts?: WaContact[];
  messages?: WaMessage[];
  statuses?: Record<string, unknown>[]; // Recibos de entrega; no son mensajes del cliente.
}

/** Fast acknowledgement for Meta; processing happens after parsing. */
export interface WebhookAck {
  status: "RECEIVED";
  messages: number; // Mensajes del payload aceptados. — minimum=0.0
  duplicates: number; // Ignorados por idempotencia. — minimum=0.0
}

export interface WhatsAppSimulationResult {
  delivery_mode: "SIMULATED";
  results: ChannelTurnResult[];
}

/** Body of the Meta Cloud API webhook; the simulation endpoint accepts the same body. */
export interface WhatsAppWebhookPayload {
  object: "whatsapp_business_account";
  entry: WaEntry[];
}
```

---

## G. Errores

Forma única de todos los errores (verificado):

```json
{"error": {"code": "GUIA_NO_ENCONTRADA", "message": "…texto en español para el usuario…", "details": {}, "request_id": "uuid"}}
```

`message` ya está redactado para el usuario final. `details` aporta contexto técnico (en los 422 de
validación: `details.campos[]` con `loc`, `msg` y `type`). `code` es estable: programa contra
`code`, no contra `message`.

| HTTP | `code` | Situación | Comportamiento recomendado |
|---|---|---|---|
| 401 | `FIRMA_INVALIDA` | Webhook de WhatsApp con firma incorrecta | No aplica a la UI |
| 403 | `NO_AUTORIZADO` | `X-Admin-Token` ausente o incorrecto | Bandeja: pedir de nuevo el token o la configuración. La UI de cliente nunca llama a estos endpoints |
| 404 | `GUIA_NO_ENCONTRADA` | La guía no existe en el TMS | Mostrar `message`, permitir corregir y ofrecer asesor |
| 404 | `NO_ENCONTRADO` | Sesión inexistente o ajena, handoff o `attachment_id` inexistente, ruta inexistente | Chat: borrar `session_id` y reenviar sin él (D.8). Otros: mostrar `message` |
| 422 | `VALIDACION` | Campos inválidos, transición de handoff no permitida, mensaje duplicado en proceso | Mostrar errores por campo desde `details.campos`; si es un duplicado, reintentar con el mismo `message_id` |
| 422 | `GUIA_FORMATO_INVALIDO` | Formato de guía inválido | Validación inline del input |
| 422 | `RUTA_SIN_TARIFA`, `CIUDAD_NO_RECONOCIDA`, `SERVICIO_NO_DISPONIBLE`, `EXCEDE_LIMITE_COBERTURA` | La cotización no es posible | Mostrar `message` y ofrecer asesor; no mostrar un valor |
| 429 | `LIMITE_DE_TASA` | Más de 30 mensajes por minuto por usuario | Deshabilitar el envío ~60 s (`details.reintentar_en_segundos`; **no** hay header `Retry-After`) |
| 500 | `ERROR_INTERNO` | Error no controlado (no expone detalles) | Mensaje genérico con `request_id` para soporte; reintentar con el mismo `message_id` es seguro |
| 503 | `NO_CONFIGURADO` | Funcionalidad sin configurar (credenciales de WhatsApp, ASR, Ollama para el reindex) | Ocultar o deshabilitar la función |
| 503 | `LLM_NO_DISPONIBLE` | Ollama caído en el reindex | Reintentar más tarde. En el chat, la caída del LLM **no** da 503: responde con texto y opción de asesor |
| 400 | (sin sobre) | El preflight CORS fue rechazado (origen no permitido) | Configurar `CORS_ALLOW_ORIGINS` |

Catálogo completo de códigos y mensajes por defecto (de `app/contracts/common.py`):

| HTTP | `code` | Mensaje por defecto (`message`) |
|---|---|---|
| 401 | `FIRMA_INVALIDA` | La firma de la solicitud no es válida. |
| 403 | `NO_AUTORIZADO` | No tienes autorización para realizar esta operación. |
| 404 | `GUIA_NO_ENCONTRADA` | No encontramos una guía con ese número. Verifica que esté bien escrita. |
| 404 | `NO_ENCONTRADO` | No encontramos el recurso solicitado. |
| 422 | `CIUDAD_NO_RECONOCIDA` | No reconozco esa ciudad en el directorio de cobertura disponible. |
| 422 | `EXCEDE_LIMITE_COBERTURA` | El envío supera el peso o el volumen máximo que atendemos en ese destino. |
| 422 | `GUIA_FORMATO_INVALIDO` | El número de guía no tiene un formato válido. Debe verse así: TLX-123456-CO. |
| 422 | `RAG_SIN_EVIDENCIA` | No encontramos información suficiente para responder con seguridad. |
| 422 | `RUTA_SIN_TARIFA` | No tengo tarifa para esa ruta en la matriz disponible. |
| 422 | `SERVICIO_NO_DISPONIBLE` | Ese servicio no está disponible para el destino indicado. |
| 422 | `VALIDACION` | Revisa los datos enviados: hay campos inválidos o incompletos. |
| 429 | `LIMITE_DE_TASA` | Recibimos demasiadas solicitudes seguidas. Espera un momento e intenta de nuevo. |
| 500 | `ERROR_INTERNO` | Ocurrió un error inesperado. Intenta de nuevo más tarde. |
| 503 | `LLM_NO_DISPONIBLE` | El asistente no está disponible en este momento. Intenta de nuevo en unos minutos. |
| 503 | `NO_CONFIGURADO` | Esta función no está configurada en este entorno. |

---

## H. Seguridad

| Tema | Comportamiento real | Implicación para el frontend |
|---|---|---|
| Autenticación de clientes | **No hay login.** El cliente se identifica con su `client_id` (UUID v4 generado por la UI) | Tratar `client_id` como una credencial débil: no exponerlo en URLs ni en logs |
| Endpoints administrativos | `/api/v1/handoffs*` y `/api/v1/admin/rag/reindex` exigen `X-Admin-Token` (comparación en tiempo constante); sin él o con uno incorrecto → 403 | La bandeja del asesor necesita un backend propio (BFF) o un entorno interno: **el token no puede ir en el bundle público** |
| Roles y usuarios de asesor | **No existen**: un único token compartido, sin identidad del asesor ni auditoría por usuario | Limitación conocida (PENDIENTE) |
| CORS | Solo los orígenes de `CORS_ALLOW_ORIGINS`; métodos GET, POST, PATCH | Añadir cada origen de la UI (dev, staging…) |
| Límites de entrada | Texto del chat ≤ 4096; transcripción PBX ≤ 2000; adjunto ≤ 10 MB, solo PDF/JPEG por contenido; montos COP enteros estrictos; ≤ 30 mensajes/min por usuario | Validar en el cliente para dar mejor UX; el servidor valida igual |
| Sesiones | Una sesión solo responde a su `client_id` y canal (otro → 404, sin revelar si existe); expira a los 30 min | Ver D.8 |
| **Historial** | `X-Client-Id` es **opcional**: si se omite, el historial se devuelve a quien conozca el `session_id` | **Limitación de seguridad**: enviar siempre `X-Client-Id`, no exponer `session_id` en URLs compartibles. Corregirlo exige un cambio de contrato (header obligatorio) |
| **Métricas** | `GET /metrics/channels` es público | Limitación menor: datos agregados, sin PII |
| `/docs` y `/openapi.json` | Públicos | Desactivar o proteger en producción |
| PII | El backend no devuelve nombres completos, teléfonos, placas ni direcciones; el id externo se guarda como hash HMAC; los logs redactan PII | La UI no debe pedir datos personales en texto libre al bot. Los textos `TEXT` del RAG se generan con comprobaciones anti-PII, pero renderízalos siempre como texto plano |
| Prompt injection | Los intentos obvios ("ignora tus instrucciones…") se rechazan sin llamar al LLM; el LLM no ejecuta acciones | Ninguna acción |
| Rate limit | En memoria, por proceso: con varias réplicas cada una cuenta aparte | Limitación de despliegue |
| XSS | Ningún campo contiene HTML pensado para renderizar | **Nunca** usar `innerHTML` o `dangerouslySetInnerHTML` con textos del backend |
