# Cotización por perfiles tarifarios (CR-003, contrato 1.5.0)

Motor tarifario `excedente-v1`, que lee las tarifas de perfiles versionados guardados en PostgreSQL y
nunca del código. Las tarifas actuales son **solo de prueba** (perfil `TEST_ONLY`) y no representan
precios comerciales aprobados. Las decisiones están en `contracts/DECISIONS.md` §7 (DEC-TAR1 a DEC-TAR7).

## Fórmula

```
peso_volumétrico = alto × ancho × largo / 4000          (2 decimales, half-up)
P                = máx(peso real, peso volumétrico)      (facturable, todos los destinos)
flete            = B + máx(0, P − L) × A                 (el excedente empieza justo después de L)
IVA              = flete × 19 %                          (tasa del perfil; COP entero, half-up)
total            = flete + IVA
```

`B`, `L`, `A`, la tasa de IVA y el divisor vienen del perfil activo. El origen se registra pero no
cambia el precio. Peso y medidas aceptan hasta 2 decimales y nunca se redondean al kilogramo superior.

## Matriz de prueba (`matriz-pruebas` v1, TEST_ONLY; la carga la migración 0004)

| Destino (DANE) | Base | Peso incluido | Excedente/kg | 6,2 kg: flete + IVA = total |
|---|---:|---:|---:|---:|
| Barranquilla (08001) | 28.000 | 5,5 kg | 5.000 | 31.500 + 5.985 = **37.485** |
| Bucaramanga (68001) | 19.300 | 5 kg | 3.600 | 23.620 + 4.488 = 28.108 |
| Cali (76001) | 19.800 | 5 kg | 3.700 | 24.240 + 4.606 = 28.846 |
| Cartagena (13001) | 26.900 | 5,5 kg | 4.800 | 30.260 + 5.749 = 36.009 |
| Cúcuta (54001) | 23.800 | 5 kg | 4.400 | 29.080 + 5.525 = 34.605 |

Las columnas de 6,2 kg suponen un paquete pequeño, en el que el peso real es el facturable.

## Uso

```bash
curl -s -X POST http://localhost:8000/api/v1/cotizaciones -H 'Content-Type: application/json' \
  -d '{"origen":"Bogotá D.C.","destino":"Barranquilla","peso_real_kg":6.2,"alto_cm":20,"ancho_cm":30,"largo_cm":40}'
```

- La respuesta (201) trae el desglose completo, la versión del perfil y `precio_comercial: false`.
- Cada cotización queda guardada en `tariff_quotations` con **todos** los valores usados, así que un
  cambio de tarifa posterior no la altera.

| Error | Cuándo |
|---|---|
| 422 `TARIFA_NO_DISPONIBLE` | El destino no tiene regla, p. ej. Medellín |
| 422 `CIUDAD_NO_RECONOCIDA` | La ciudad no está en el directorio |
| 422 `CIUDAD_NO_CUBIERTA` | La ciudad está reconocida pero no tiene cobertura |
| 422 `VALIDACION` | Peso o medidas inválidos, o con más de 2 decimales |
| 503 `NO_CONFIGURADO` | No hay un perfil activo y vigente del tipo `TARIFF_ENVIRONMENT` |

`POST /cotizar` (`provisional-v1`, usado por el chat) **no cambió**.

## Cambiar tarifas

Un perfil nunca se edita: se carga la versión siguiente y se activa. Al activarla, la anterior del
mismo tipo se retira en la misma transacción, y nunca hay dos perfiles activos del mismo tipo.

```bash
cd backend
uv run alembic upgrade head                                   # 0004: tablas y semilla
uv run python -m app.tariffs.cli show
uv run python -m app.tariffs.cli load --file perfil-v2.json   # queda INACTIVE
uv run python -m app.tariffs.cli activate --profile-id <uuid>
```

Formato del JSON:

```json
{"name": "matriz-pruebas", "version": 2, "environment_type": "TEST_ONLY", "iva_pct": "19.00",
 "volumetric_divisor": 4000, "effective_from": null, "effective_until": null,
 "rules": [{"destination_city_code": "08001", "base_price_cop": 29000,
            "included_weight_kg": "5.50", "excess_price_per_kg_cop": 5200}]}
```

Se rechaza una versión repetida, un destino repetido o un código DANE que no esté en el directorio.
Un perfil `COMMERCIAL` solo se usa con `TARIFF_ENVIRONMENT=COMMERCIAL`.

## Pendiente

- **IVA:** contabilidad debe confirmar si aplica. El transporte de carga terrestre puede estar excluido
  según el art. 476 del E.T. y la mensajería está gravada. La tasa se cambia en el perfil, sin tocar
  código.
- **Seguro, recargos de frío y peligrosa, y otros componentes:** sin definir, así que no se incluyen
  (DEC-TAR4).
- **Perfil comercial:** no existe todavía. El modo por defecto es `TEST_ONLY`.

## Pruebas

`tests/tariffs/` contiene 51 pruebas:

- **Unitarias** (las 30 combinaciones de la matriz de la spec 1.1 §11):
  - peso por debajo de `L`, igual, `L` + 0,01, fracciones, 7,25 kg y 50 kg;
  - IVA y redondeo;
  - peso volumétrico y cuantización *half-up*;
  - vigencia de perfiles;
  - validación de 2 decimales;
  - aritmética de la respuesta.
- **Integración con PostgreSQL:**
  - snapshot completo;
  - el origen no cambia el precio;
  - peso facturable;
  - errores;
  - perfil inactivo o vencido;
  - COMMERCIAL ignorado en modo TEST_ONLY;
  - snapshot intacto tras activar la versión 2;
  - validación de carga;
  - OpenAPI real igual al contrato.
