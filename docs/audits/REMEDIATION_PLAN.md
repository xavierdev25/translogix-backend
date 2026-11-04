# Plan de corrección — auditoría del 2026-11-04

**Regla aplicada:**
- Se corrigieron solo los hallazgos **CRÍTICOS y ALTOS inequívocos** que no requieren una decisión de
  negocio, cada uno con su prueba.
- Lo demás queda con una pregunta concreta y una recomendación (§3).
- Las referencias de línea corresponden al árbol de trabajo tras las correcciones (contrato 1.6.0).
- La evidencia numérica está en [`TEST_EVIDENCE.md`](TEST_EVIDENCE.md).

## 1. Corregido

### REL-01 — 500 bajo ráfagas de preguntas con IA (ALTO)

- **Evidencia:** 24 preguntas RAG simultáneas → 10 × HTTP 500. El log dice
  `QueuePool limit of size 5 overflow 10 reached … timeout 30.00`; p50 37,5 s.
- **Archivos:** `backend/app/main.py:263` (pool propio de la recuperación RAG),
  `backend/app/shared/llm.py:48` (`Admission`) y `:111` (uso),
  `backend/app/shared/config.py` (`llm_max_inflight=4`, `llm_max_queue=8`).
- **Impacto:** en un pico, el cliente recibía "error inesperado" tras 30 s en lugar de una respuesta o
  una oferta de asesor.
- **Corrección:**
  1. La recuperación RAG usa su propio pool. Antes tomaba una segunda conexión del pool que el turno ya
     ocupaba: adquisición anidada.
  2. Admisión acotada al LLM: hasta 4 llamadas en curso y 8 en espera. Por encima, la ruta degradada
     de `LLM_NO_DISPONIBLE` responde al instante.

  Ver DEC-AUD2 y DEC-AUD3.
- **Prueba:**
  - `tests/test_llm_admission.py` (cola, liberación y pool separado);
  - saturación de 24 y 40 peticiones → **0 errores**; p50 1,6 s y 1,0 s.
- **Estado:** CORREGIDO.
- **Residual:** los valores 4 y 8 están medidos en este equipo; hay que volver a medirlos al cambiar de
  hardware. Si el pool principal se agota por otra causa, la respuesta sigue siendo 500 (no medido).

### SEC-04/05 — Cuerpo sin límite; adjuntos almacenados antes de validar el tamaño (ALTO)

- **Evidencia:**
  - JSON de 25 MB → 422 después de leerlo entero; RSS 76,4 → 181,6 MB.
  - Subida de 30 MB (límite 10 MB) → 422 tras recibirla entera.
- **Archivos:** `backend/app/shared/http.py:61` (`BodyTooLarge`) y `:69` (`BodySizeLimitMiddleware`);
  `backend/app/main.py:842` (límites por prefijo); `backend/app/contracts/common.py`
  (`CUERPO_DEMASIADO_GRANDE`, 413); `backend/app/contracts/openapi.py` (documentado en toda operación
  con cuerpo).
- **Impacto:** agotamiento de memoria o disco con pocas peticiones, sin autenticación.
- **Corrección:**
  - se rechaza antes de leer si `Content-Length` supera el límite, y se corta en *streaming* si no lo
    declara;
  - 1 MiB por defecto;
  - adjuntos: 10 MiB + 64 KiB;
  - el lote B2B conserva su propio 413;
  - la respuesta sale dentro de CORS y lleva `X-Request-ID`.

  Ver CR-008 y DEC-AUD1.
- **Prueba:**
  - `tests/test_http_body_limit.py` (8);
  - curl contra el servidor: 413 con **0 bytes subidos** y RSS sin cambio.
- **Estado:** CORREGIDO.

### CON-01 — Actualización perdida con turnos simultáneos de una conversación (ALTO)

- **Evidencia:** "40x50x60" y "45,5 kg" enviados a la vez → uno sobrescribió al otro y el bot volvió a
  preguntar.
- **Archivos:** `backend/app/db/repositories.py:48` y `:56` (`for_update`),
  `backend/app/modules/conversation/service.py:137` y `:151`.
- **Impacto:** datos de la cotización o de la PQRS perdidos. Ocurre con dobles envíos en web y con
  WhatsApp, que agrupa mensajes.
- **Corrección:** `SELECT … FOR UPDATE` en la resolución del turno. El segundo turno espera al commit
  del primero y conserva la atomicidad del turno (DEC-AUD4).
- **Prueba:**
  - `tests/conversation/test_conversation_lock.py` (2);
  - sonda CON-01 contra el servidor real → `QUOTE_SUMMARY` con 45,5 kg y $190.975.
- **Estado:** CORREGIDO.
- **Nota:** dos primeros mensajes simultáneos de un usuario sin sesión pueden crear dos conversaciones
  (no medido; impacto bajo).

### AI-01 — El RAG escalaba preguntas con respuesta en el corpus (ALTO)

- **Evidencia:** golden set 15/18 con el juez revisado. G04, G05 y G10 escalaban por CRIT-001 con
  confianza entre 0,66 y 0,74, aunque la respuesta estaba en la lista de prohibidos.
- **Archivos:** `backend/app/modules/rag/service.py:227` (margen sobre el *score* híbrido) y `:121`
  (regla 4 del *prompt*).
- **Impacto:**
  - precisión de la IA (rúbrica 1.5);
  - escaladas innecesarias a asesores, que son el costo que el caso quiere reducir.
- **Corrección:**
  - el margen se mide sobre el *score* que ordena los *chunks*;
  - el *prompt* ordena responder con lo que dice un documento aunque el ítem esté dentro de una lista.
- **Prueba:** golden set **18/18**, 0 alucinaciones y 5/5 abstenciones correctas, en dos ejecuciones
  posteriores.
- **Estado:** CORREGIDO.

### R1 — El chat prometía "Te responderá en este mismo chat" (ALTO)

- **Archivos:** `backend/app/modules/conversation/orchestrator.py:687`;
  `docs/api-examples/chat.en_espera_asesor.json`; `docs/FRONTEND_INTEGRATION_EXAMPLES.md`.
- **Impacto:** promesa falsa al cliente. No existe canal asesor→cliente.
- **Corrección provisional:** "Tu caso ya quedó registrado y está en la cola de asesores de TransLogix."
  (DEC-AUD5). Lo definitivo, la llamada al teléfono capturado, llega con CR-007.
- **Prueba:** `tests/conversation/test_chat_flows.py::test_waiting_for_an_advisor_does_not_promise_a_reply_in_this_chat`.
- **Estado:** CORREGIDO (provisional).
- **Acción para el front:** si muestra este texto fijo o lo compara, debe actualizarlo.

### TST-01 (parcial) — Red de regresión del núcleo

- **Corrección:** 6 pruebas de flujo de chat contra la base de test, con Ollama desactivado:
  - rastreo;
  - fórmula oficial (190.975);
  - CRIT-002 y CRIT-005;
  - degradación sin LLM;
  - texto de espera del asesor.
- **Resultado:** cobertura total del 73 % al 80 %.
- **Pendiente:** PQRS (36 %), corpus RAG (37 %) y servicio RAG (42 %) (ver §2).

## 2. Pendiente sin decisión de negocio (se puede hacer ya)

| Id | Severidad | Archivo | Corrección propuesta | Prueba que lo demostrará |
|---|---|---|---|---|
| TST-01 | MEDIO | `backend/app/modules/pqrs/service.py`, `backend/app/modules/rag/corpus.py`, `backend/app/modules/rag/service.py` | Pruebas de los plazos de PQRS (avería oculta de 3 días, 10 días hábiles con festivos), del troceado 512/64 y de los controles deterministas de la respuesta RAG (números fuera de la evidencia, PII, URL) con un LLM falso | Cobertura ≥ 70 % en esos módulos |
| REL-02 | MEDIO | `backend/app/modules/rag/service.py:383` (bucle de `reindex`) | Aislar el error por documento: registrar y continuar; el informe lista los omitidos | Prueba con un documento ilegible: los demás se indexan |
| OPS-01 | MEDIO | `backend/app/shared/http.py:185` | Documentar que el limitador es por proceso. Con más de un *worker*, mover el estado a PostgreSQL (tabla de ventanas) antes de considerar Redis | Prueba con dos instancias de app que comparten el límite |
| LLM-01 | BAJO | `backend/app/shared/llm.py` | Un reintento con espera corta, solo ante `ConnectError` y dentro del presupuesto de 30 s | Prueba con un transporte que falla una vez |
| DEMO-01 | BAJO | `data/source/dataset_guias_simuladas_tms.json` | **No inventar datos.** Para demostrar `RELECTURA_HUB_FONTIBON`, pedir al negocio una guía de ejemplo con ese estado, o mostrar el mapa de estados en la exposición | Sonda TRK-02 con una guía real |
| INF-04 | INFORMATIVO | servidor `:8000` (túnel) | Reiniciarlo cuando el front esté avisado, para publicar 1.6.0 | `GET /openapi.json` → `1.6.0` |

## 3. Decisiones necesarias (no se implementó ningún supuesto)

| # | Hallazgo | Pregunta concreta | Recomendación |
|---|---|---|---|
| **Q1** | BIZ-01, ALTO | ¿Cómo se trata un envío B2B con `mercancia_peligrosa=true` mientras no exista una política documentada por clase? Hoy se acepta y solo se registra (DEC-SHP7 PROVISIONAL) | Crear el envío con `review_status = PENDIENTE` (motivo `MERCANCIA_PELIGROSA`) y que no avance hasta que lo apruebe un REVISOR_ESPECIALIZADO, como propone CR-006. No aceptarlo automáticamente (spec §8). Requiere aprobar CR-006 |
| **Q2** | SEC-01, ALTO | ¿Se exige `X-Client-Id` en `GET /chat/sessions/{id}/history`? Hoy, sin él, basta con el `session_id`. Exigirlo endurece una validación: es MAJOR según el CHANGELOG | Sí. El front ya conoce el `client_id`, porque lo envía en cada mensaje. Coordinar la fecha con el front y publicar 2.0.0, o pactar la excepción previa a la Puerta A |
| **Q3** | SEC-06, ALTO | ¿Cómo se limita `POST /attachments`, que hoy no tiene autenticación ni límite? (a) Por IP: hay que decidir si se confía en `CF-Connecting-IP`, que llega por el túnel de Cloudflare. (b) Exigir un `session_id` de una conversación abierta y limitar por sesión | (b). No depende de cabeceras del proxy y ata el adjunto a un caso real. Vuelve obligatorio un campo (MAJOR): hacerlo en la misma versión que Q2 |
| **Q4** | SEC-03, MEDIO | ¿Se desactivan `/docs` y `/openapi.json` en producción? | Sí, con una variable de entorno: activos en dev y en el túnel, inactivos en producción. El contrato ya se publica en `contracts/openapi.yaml` |
| **Q5** | PII-01, ALTO | ¿Se aprueba la propuesta de cifrado del §4 (dependencia y gestión de claves)? ¿Cuáles son los plazos de retención y eliminación? | Aprobar §4. Fijar la retención con el área legal (Ley 1581 de 2012). Mientras tanto, no habilitar producción |
| **Q6** | INF-02 | ¿CRIT-003 y CRIT-005 se aplican en modo contextual (actual) o literal? El literal escalaría todo envío con valor declarado > 5 M y toda mercancía peligrosa, aunque no haya reclamación ni incidente | Mantener el modo contextual, que es coherente con el propósito de la regla, y que el negocio lo apruebe por escrito. **No se cambió** |
| **Q7** | HITL-03 | ¿Qué NIT o cuentas son `CORPORATIVO_GOLD`, para demostrar CRIT-004? | Que el negocio entregue la lista. No se inventan datos de *fixture* |
| **Q8** | HITL-02 | ¿Qué debe pasar tras 5 minutos de inactividad (`tiempo_maximo_inactividad_minutos`)? ¿Recordatorio, cierre o transferencia? | Recordatorio a los 5 min y cierre a los 30 (TTL actual). Solo en web chat; en WhatsApp lo limita la ventana de 24 h de Meta |
| **Q9** | R1 / R2 | Respuestas del front a CR-007 (etapas del tablero, captura del destinatario) | Ver [`RESPUESTA_FRONT_CRM.md`](../RESPUESTA_FRONT_CRM.md). Con la pregunta 1 respondida empieza P0a (contrato 1.7.0) |
| **Q10** | CAP-01, ALTO | ¿En qué infraestructura corre el LLM en producción? ¿Cuál es el volumen pico por hora esperado? | Medir con la mezcla real: el 55 % es rastreo, determinista. Dimensionar con GPU o varias instancias de Ollama y repetir `run_load.py` y la saturación antes de afirmar capacidad |

## 4. Propuesta de cifrado de PII (Q5, pendiente de aprobación)

**Alcance:**
- `shipment_recipients`: `nombre`, `numero_documento`, `telefono`, `email`, `direccion`;
- el texto del usuario en `messages.content_json`;
- los datos de contacto que traiga CR-007 (leads).

**Diseño:**
- Cifrado de aplicación con **Fernet** (AES-128-CBC + HMAC-SHA256, autenticado) mediante `MultiFernet`,
  que permite rotar claves (la primera cifra; todas descifran).
- Para buscar por teléfono, documento o email sin descifrar: un **índice ciego** HMAC-SHA256 con una
  clave separada, en columnas `*_bidx`.
- Tipo SQLAlchemy `EncryptedString` en `app/shared/crypto.py`.

**Claves:**
- `PII_ENCRYPTION_KEYS` (lista, la primera es la activa) y `PII_BLIND_INDEX_KEY`;
- nuevas en `EnvVar` y en `SECRET_ENV_VARS`;
- vienen del entorno o de un gestor de secretos y **nunca** se guardan en el repositorio.
- Los tests generan claves efímeras en la propia prueba.
- El arranque falla si faltan en un entorno que no sea de desarrollo.

**Dependencia nueva:** `cryptography` (hoy no está instalada; se fija la versión en `uv.lock`).

**Migración 0005** (**antes** que la del CRM, que pasaría a 0006):
- añade las columnas `*_bidx`;
- cifra las filas existentes en lotes;
- `downgrade` descifra y quita las columnas;
- se prueba *upgrade* → *downgrade* → *upgrade* sobre una copia con datos y se verifica el recuento.

**Contrato:** sin cambios; el cifrado no es visible en la API. Es un CR interno, sin bump.

**Queda fuera:** retención y eliminación (borrado o anonimización a los N días, y solicitud del titular).
Requiere la decisión legal de Q5.

**Prueba de aceptación:**
- con SQL directo no aparece ningún valor en claro;
- la búsqueda por teléfono funciona por el índice ciego;
- la rotación de la clave mantiene la lectura;
- la latencia de `/shipment-batches` se mide antes y después.
