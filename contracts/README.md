# Contratos de integración — TransLogix AI (dueño: P01)

Fuente de verdad compartida entre los agentes. Solo P01 escribe en `contracts/` y
`backend/app/contracts/`; los demás proponen cambios en `docs/contract-change-requests.md`.

## Qué hay

| Ruta | Contenido | ¿Se edita? |
|------|-----------|-----------|
| `backend/app/contracts/` | Modelos Pydantic v2 (**canónicos**), 17 puertos (`ports.py`), un fake por puerto (`fakes.py`), superficie HTTP (`api_spec.py`), utilidades de referencia (guía, PII, dinero, zona horaria). | Sí (P01) |
| `contracts/examples/` | 89 ejemplos *golden*: uno por endpoint y por código de error, y `ConversationReply` completo para S1–S16. | Sí (P01) |
| `contracts/vectors/` | Vectores de conformidad (guía, hash de usuario, PII) que deben reproducir P02 y P04. | Sí (P01) |
| `contracts/openapi.yaml` | OpenAPI 3.1 generado: errores con `x-error-codes`, `X-Request-ID`, ejemplos, eventos WS en `x-websockets`. | No: se genera |
| `contracts/schemas/` | JSON Schema 2020-12 de los contratos entre servicios (InboundMessage, ConversationReply, HandoffPayload, RagAnswer, WsEvent…). | No: se genera |
| `contracts/mocks/manifest.json` | Operación → ejemplos de petición y respuesta, con escenario y pista de *match*. | No: se genera |
| `contracts/types/api.d.ts` | Tipos TypeScript (`openapi-typescript@7.13.0`); `openapi.sha256` registra de qué YAML salieron. | No: se genera |
| `contracts/DECISIONS.md` | Cómo el contrato implementa las DEC del proyecto + decisiones propias (`DEC-C*`). | Sí (P01) |
| `contracts/CHANGELOG.md` | Versiones y cambios respecto al documento de contratos. | Sí (P01) |

## Cómo se consume

**Servicios de backend:**

```python
from app.contracts.inbound import InboundMessage
from app.contracts.reply import ConversationReply
from app.contracts.common import ContractError, ErrorCode
from app.contracts.normalizers import extract_numero_guia
```

Errores: lanzar `ContractError(ErrorCode.X)`; la capa HTTP responde `ERROR_HTTP_STATUS[code]`
con `error.to_envelope(request_id)`.

**Cualquier módulo, sin esperar a los demás:** programar contra `app.contracts.ports` y probar con
`app.contracts.fakes` (determinista, basado en los ejemplos golden y en el dataset simulado del TMS).

```python
from app.contracts import fakes, ports

core: ports.ConversationService = fakes.FakeConversationService()
reply = await core.handle(inbound_message)   # cualquier canal; respuesta golden o simulada
tms: ports.TmsPort = fakes.FakeTms()         # las 5 guías: registro interno y vista segura
```

**Frontend (P15):** los tipos ya están generados en `contracts/types/api.d.ts`. Para MSW, recorrer
`contracts/mocks/manifest.json`: cada operación trae sus respuestas con `status`, `example` (ruta
relativa a `contracts/`), `match` (`path`, `body` como subconjunto, `text_contains` o `null` =
respuesta por defecto) y `pendiente_de_servicio_real`; `errors` trae un ejemplo por código posible.

## Garantías que dan los modelos

Un modelo que las incumple no se puede construir:

- fechas con offset, servidas en `America/Bogota`; dinero entero en COP;
- ningún texto visible al cliente acepta PII (el TMS crudo sí la trae: hay que sanear);
- la cotización cuadra y respeta el flete mínimo; el deducible y los faltantes del ticket cuadran;
- RAG sin evidencia no lleva texto, con evidencia lleva al menos una cita;
- el texto hablado de PBX no lleva dígitos, Markdown, URLs ni emoji, y tiene como máximo 40 palabras;
- los campos extra se rechazan en entrada y en salida.

## Regenerar y verificar

```bash
cd backend && uv run python -m app.contracts
```

```bash
contracts/types/generate.sh
```

```bash
cd backend && uv run pytest && uv run ruff check . && uv run mypy && uv run python -m app.contracts --check
```

## Nota sobre carpetas compartidas

Para poder ejecutar estas pruebas antes de que existieran P00 y P02, P01 dejó una semilla en
carpetas ajenas: `data/source/` y `.env.example` (P00), `backend/pyproject.toml` y `backend/uv.lock`
(P02). Sus dueños las adoptan; el detalle está en `docs/contract-change-requests.md`.
