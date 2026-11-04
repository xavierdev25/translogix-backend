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

_Sin solicitudes todavía._
