# Revisión de la especificación 1.1 frente a lo implementado

Comparo la especificación 1.1 (evolución comercial y logística) con el código real del contrato 1.3.0
y la migración 0002. **De la 1.1 están implementados CR-002** (identidades internas, 1.4.0, migración 0003) **y CR-003**
(perfiles tarifarios, 1.5.0, migración 0004); lo demás sigue pendiente de aprobación. Así lo exige su §10 y la regla 1 de
`docs/contract-change-requests.md`: primero se registra y aprueba el cambio de contrato.

Este documento contiene los huecos, los conflictos con el P0 existente y las ambigüedades que el negocio
debe resolver. Los cambios propuestos están en `docs/contract-change-requests.md`, de CR-002 a CR-006.

## 1. Qué existe hoy y qué pide la 1.1

| Pide la 1.1 | Existe hoy | Hueco |
|---|---|---|
| Perfiles tarifarios versionados TEST_ONLY y COMMERCIAL, en tablas (§5.1–5.2) | Fórmula `provisional-v1` leída del CSV, por **ruta origen-destino** (25 rutas), con peso volumétrico, seguro y recargos. No está marcada como TEST_ONLY | Motor y tablas nuevas (CR-003) |
| Fórmula `T = B + max(0, P − L) × A`, peso exacto, sin redondear (§6.1) | No existe | CR-003 |
| Snapshot con base, peso incluido, excedente, perfil y tipo (§6.5) | Snapshot P0: `cotizacion_id`, `formula_version`, `tarifa_version` (hash del CSV) y `cotizado_en` | Campos nuevos (CR-003) |
| Gestión comercial con asesor y estados controlados (§4 B, §5.3–5.4) | No existe | CR-004 |
| Asesor como identidad interna autenticada (§5.3) | **Resuelto en 1.4.0 (CR-002):** usuarios internos, roles y token personal | — |
| Entrega a agencia separada de su recepción (§4 C, §5.5) | No existe. La tabla `handoffs` actual es la escalación chat → asesor, un concepto distinto | CR-004 (tabla `agency_handoffs`, otro nombre a propósito) |
| Estados logísticos, historial inmutable y actualización manual (§4 D, §5.6, §7) | `shipments.estado` con un único valor `REGISTRADO` (DEC-SHP5) y sin historial. El tracking público es de solo lectura y no muestra guías internas | CR-005 |
| Mercancía peligrosa: registrar y derivar a revisión, sin aceptación automática (§8) | **Conflicto:** el P0 crea el envío y asigna guía sin revisión (DEC-SHP7) | CR-006 |
| Cadena de frío: registrar la temperatura y validarla contra la capacidad (§8) | Solo se valida el booleano `servicio_refrigerado_disponible`. **El directorio no trae rangos de temperatura** | CR-006 |
| Cifrado y retención de PII antes de producción (§9) | PII en claro en `shipment_recipients` (DEC-SHP9) | Decisión D1 (§3) |
| Worker durable para lotes (P1) | Reanudación por lease y repetición idéntica, sin worker | Fuera de estos CR |

Lo demás del P0 se conserva sin cambios y sus pruebas siguen pasando (442).

## 2. Conflictos que hay que resolver antes de implementar

1. **Fórmula oficial del reto contra fórmula 1.1.** El PDF del reto define el peso volumétrico
   (`alto × ancho × largo / 4000`), el peso facturable (`max(real, volumétrico)`) y el seguro (1 % del
   valor declarado, con mínimo). La 1.1 usa el "peso exacto" `P` y no menciona volumétrico, seguro ni
   recargos. **Propuesta (CR-003):** conservar `POST /cotizar` (`provisional-v1`) tal cual, porque lo
   usan el chat, el demo y 408 pruebas de contrato, y crear el motor por perfiles como recurso nuevo.
   Sustituir `/cotizar` sería un cambio MAJOR.
2. **Perfil por destino contra matriz por ruta.** Las reglas 1.1 dependen solo del destino; la matriz
   actual depende del origen y del destino. Hay que decidir si el origen deja de influir en la tarifa.
3. **Lotes B2B contra flujo comercial.** El P0 crea envíos directamente, sin asesor, mientras que la 1.1
   pone el asesor entre la cotización y la agencia. Hay que decidir si los lotes B2B entran en la gestión
   comercial o pasan directo a `PENDIENTE_RECEPCION`. **Propuesta:** directo, porque son cuentas
   empresariales con acuerdo previo.
4. **Momento de creación del envío en el flujo comercial.** `agency_handoffs` exige un `shipment_id`,
   así que el envío (con su guía y destinatario) debe existir antes de entregarse a la agencia.
   **Propuesta:** se crea al pasar a `LISTO_PARA_AGENCIA`, cuando los datos están completos.
5. **Mercancía peligrosa en lotes ya implementados.** Hoy el P0 la acepta sin revisión. **Propuesta
   (CR-006):** se crea el envío marcado `requiere_revision`, y no puede pasar a `EN_CURSO` hasta que lo
   apruebe un revisor.

## 3. Decisiones de negocio pendientes (P0.1 y ambigüedades de la 1.1)

| Id | Decisión | Propuesta por defecto | Bloquea |
|---|---|---|---|
| D1 | Cifrado de PII, claves, rotación, retención, respaldos y auditoría de lectura | Cifrado de campo en la aplicación (AES-256-GCM) con la clave en un gestor (KMS o Vault); columna `key_id` para rotar; auditar cada lectura y escritura de `shipment_recipients` | Producción |
| D2 | ¿El excedente empieza justo después de `L` o hay un umbral propio? (§6.4: "incluye hasta 5,5 kg y cobra desde 6 kg") | Justo después de `L`, como la fórmula aprobada. No se añade umbral sin aprobación | Motor tarifario |
| D3 | Precisión del peso y redondeo del importe | Peso a 0,01 kg. El importe se redondea a COP entero con `ROUND_HALF_UP` (como DEC-Q8). Con la matriz de prueba y 0,01 kg el resultado siempre es entero (§4 de CR-003); con 0,001 kg no (Cali, 5,001 kg → 19.803,7) | Motor tarifario |
| D4 | ¿`P` es el peso real o el facturable (incluye volumétrico)? | Peso real, tal como lo dice la 1.1 ("peso exacto"). Se registra que difiere del PDF | Motor tarifario |
| D5 | ¿La tarifa incluye seguro y recargos de frío y peligrosa? | No: `T` es solo el flete. El seguro y los recargos quedan fuera hasta que se definan en un perfil | Motor tarifario |
| D6 | ¿Qué significan exactamente "entrega a agencia" y "recepción confirmada"? | Entrega = la solicitud se envía a la agencia. Recepción = la agencia confirma que recibió el paquete o inició el servicio; es lo que habilita `EN_CURSO` | Gestión comercial |
| D7 | Lista definitiva de estados logísticos de las agencias | Usar la lista de la 1.1 y validarla con las agencias | Gestión logística |
| D8 | Reglas comerciales de aceptación (clases de mercancía peligrosa, documentos, temperaturas) | Sin política: todo pasa a revisión, nada se acepta automáticamente | Aceptación |
| D9 | Límite de 100.000 guías del formato `TLX-dddddd-CO` (DEC-SHP1) | Ampliar el patrón a 6–8 dígitos. Es un cambio MAJOR de `NumeroGuia`: hay que planificarlo antes de agotar el rango | Largo plazo |
| D10 | Gestión de usuarios internos: ¿SSO corporativo o cuentas locales? | Cuentas locales con token personal (CR-002) y migrar a SSO/OIDC después | Gestión comercial y logística |
| D11 | ¿Qué estados y campos ve el cliente B2B? | El código de estado, `occurred_at` y la etiqueta. Nunca notas, motivos internos ni `changed_by` | Consulta B2B |
| D12 | ¿Qué cotizaciones abren una gestión comercial: chat, web, B2B? | Solo las del recurso nuevo `POST /cotizaciones` (CR-003). `/cotizar` sigue siendo un cálculo sin seguimiento | Volumen de asesores |
| D13 | Un evento logístico registrado tarde (con `occurred_at` anterior al último), ¿cambia el estado actual? | Se acepta en el historial marcado como registrado tarde; el estado actual sigue el orden de registro | Gestión logística |
| D14 | ¿`PERDIDO` debe abrir además una escalación a OPERACIONES? | Sí, como incidencia crítica auditada; se define la escalación al aprobar CR-005 | Gestión logística |

## 4. Orden recomendado

1. ~~Aprobar **CR-002**: usuarios internos y roles.~~ **ACEPTADA e implementada (1.4.0)**. Ver
   [`IDENTIDADES_INTERNAS.md`](IDENTIDADES_INTERNAS.md).
2. ~~Resolver D2–D5 y aprobar **CR-003**.~~ **ACEPTADA e implementada (1.5.0)**. Ver
   [`TARIFAS_PERFILES.md`](TARIFAS_PERFILES.md).
3. ~~CR-004~~ **sustituida por CR-007** (CRM del asesor, PROPUESTA): la gestión comercial se integra
   en el lead del CRM. D6 y D12 se resuelven allí (ver `REVISION_SOLICITUD_ASESOR.md`).
4. Resolver D7 y D11 y aprobar **CR-005**: estados logísticos, historial y actualización manual.
5. Resolver D8 y aprobar **CR-006**: ajustes del P0 para mercancía peligrosa, frío y TEST_ONLY.
6. D1 (cifrado) es independiente de los anteriores, pero **bloquea producción**. D9 se planifica aparte.

Cada CR aceptado sube la versión del contrato (1.4.0, …). Llega con sus ejemplos, su migración, sus
pruebas (las de la 1.1 §11) y las de los 442 tests actuales sin regresiones.
