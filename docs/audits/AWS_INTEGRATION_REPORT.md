# Integración híbrida AWS — TransLogix AI (contrato 2.1.0, CR-011)

| | |
|---|---|
| Fecha | 2026-11-04 |
| Base real | Commit **`feef68d`** (contrato 2.0.0, migración 0007). El encargo citaba `dfb05d3`, que es el commit del RBAC (1.7.0); se verificó antes de empezar |
| Alcance | S3 para adjuntos, SQS con DLQ para la reindexación RAG y CloudWatch para métricas |
| Sin cambios | PostgreSQL y Ollama siguen locales; sin microservicios, Redis, CQRS, *event sourcing* ni motor de permisos |
| Reutilizado sin reimplementar | RBAC, propiedad por `X-Client-Id`, cifrado Fernet, limitador PostgreSQL, revisión de mercancía peligrosa y auditoría `api_audit_events` |

**Inspección previa (fase 0):**

| Aspecto | Hallazgo |
|---|---|
| Repositorio | `main` llevaba un commit local sin subir (`feef68d`, 2.0.0), que se subió después. En `origin/main` no había cambios de nadie más (sin trabajo concurrente) |
| Migraciones | Cabeza `0007` |
| `var/attachments` | 64 archivos (680 KB), **sin ninguna fila** en `attachments` (dev ni test): restos de pruebas |
| RAG | Reindexación síncrona y BM25 en memoria por proceso |
| Eventos | No existía outbox ni tabla de trabajos |
| Pruebas | 610 en verde |

## 1. Archivos creados y modificados

**Nuevos:**

| Archivo | Contenido |
|---|---|
| `backend/app/shared/storage.py` | `ObjectStorage`, `LocalObjectStorage`, `S3ObjectStorage` (SSE-KMS, escritura condicional, SigV4) y `build_storage` |
| `backend/app/shared/metrics.py` | Métricas EMF en stdout y `timer` |
| `backend/app/storage/{migration,cli}.py` | Migración local → S3: `copy`, `verify` y `cleanup` |
| `backend/app/jobs/{service,publisher,outbox,worker}.py` | `JobService` (trabajo y outbox en una transacción), `MessagePublisher`/`SqsPublisher`, `OutboxRelay` y `JobWorker` |
| `backend/app/worker/__main__.py` | `python -m app.worker run`, `once`, `sweep-dlq` y `retry` |
| `backend/migrations/versions/0008_aws_storage_jobs.py` | Migración aditiva |
| `backend/tests/aws/` | `conftest.py` (moto), `test_storage.py` (11), `test_jobs.py` (11), `test_metrics.py` (4) y `test_real_aws.py` (6, solo con AWS real) |
| `infra/aws/` | `provision.sh`, `teardown.sh`, `s3-bucket-policy.json`, `iam-policy-api.json`, `iam-policy-worker.json` y `README.md` |
| `contracts/examples/` | `attachment.link`, `attachment.content.response`, `reindex.job` y `error.almacenamiento-no-disponible` |

**Modificados:**

| Área | Archivos |
|---|---|
| Entrada HTTP | `app/main.py` (contenedor, subida con compensación, `/content`, `/link`, reindexación asíncrona y `/admin/rag/jobs/{id}`) |
| Adjuntos | `app/modules/pqrs/service.py` (`AttachmentService` sobre `ObjectStorage`) |
| RAG | `app/modules/rag/service.py` (detecta un índice reconstruido por otro proceso) |
| Persistencia | `app/db/{models,repositories}.py` |
| Servicios compartidos | `app/shared/{config,llm,http,logging,ratelimit}.py` (métricas y campos de log) |
| Contrato | `app/contracts/{api_spec,common,env,ops,attachments,examples}.py`; `contracts/openapi.yaml`, `schemas`, `mocks` y `types` regenerados |
| Proyecto | `.env.example`; `pyproject.toml` y `uv.lock` (`boto3` 1.43; dev: `moto` 5.2 y `boto3-stubs`) |
| Pruebas existentes | `tests/auth/test_rbac.py` (la guarda cuenta 31 rutas) y `tests/contracts/test_openapi.py` |
| Documentación | `contracts/{CHANGELOG,DECISIONS}.md`; `docs/{contract-change-requests,FRONTEND_CAMBIOS_2_0_0,RESPUESTA_FRONT_CRM}.md` |

## 2. Arquitectura final

```
                       ┌────────────── PostgreSQL local ───────────────┐
 PWA / B2B ─HTTPS─▶ API FastAPI ─────────┤ attachments (+storage_backend) · processing_jobs   │
   │  RBAC + X-Client-Id     │           │ outbox_messages · attachment_storage_migrations   │
   │                         │           └───────────────────────────────────────────────┘
   │   upload/download        │ job + outbox (1 transacción)
   │        ▼                 ▼
   │   ObjectStorage ──▶ S3 (privado, SSE-KMS CMK)        Worker (python -m app.worker)
   │   (local | s3)        ▲ URL prefirmada SigV4 60 s     ├─ OutboxRelay ──send──▶ SQS rag-jobs ─redrive×3─▶ DLQ
   │                       └──── navegador ────────────    └─ JobWorker ◀─long polling──┘   sweep-dlq ◀──┘
   │                                                            └─ IngestionService ──▶ Ollama local
   └─ stdout: logs JSON (sin PII) + métricas EMF ──▶ agente/awslogs ──▶ CloudWatch Logs ──▶ métricas
```

**Decisiones** (DECISIONS §11):
- **Claves de objeto:** las genera el servidor.
- **Escritura:** condicional (`If-None-Match: *`) con `ChecksumSHA256`.
- **Cifrado:** SSE-KMS con una CMK, independiente de Fernet.
- **Orden de escritura:** objeto antes que fila, con compensación si la fila falla.
- **Outbox:** una sola transacción para el trabajo y su mensaje.
- **Consumidor:** idempotente (reclamación condicional y *lease*); mensajes solo con ids.
- **Worker:** reutiliza `IngestionService`.
- **Métricas:** EMF sin llamadas síncronas a AWS.

**SQS no aumenta la capacidad de inferencia de Ollama.** Solo desacopla la reindexación del
*request*.

## 3. Variables de entorno (2.1.0)

| Variable | Defecto | Uso |
|---|---|---|
| `ATTACHMENTS_STORAGE` | `local` | `s3` exige `S3_BUCKET` y `S3_KMS_KEY_ID` |
| `AWS_REGION` | Perfil | Región de los clientes boto3 |
| `S3_BUCKET` / `S3_KMS_KEY_ID` | — | Bucket privado y ARN de la CMK |
| `JOBS_BACKEND` | `disabled` | `sqs` habilita `asincrono` y el worker |
| `SQS_QUEUE_URL` / `SQS_DLQ_URL` | — | Cola Standard y DLQ |
| `METRICS_NAMESPACE` | `TransLogix` | Espacio de nombres de CloudWatch |

Ajustes no publicados como `EnvVar`:
- `s3_presign_ttl_seconds` = 60;
- `worker_max_attempts` = 3 (debe coincidir con `maxReceiveCount`);
- `worker_visibility_timeout_seconds` = 300;
- `worker_wait_seconds` = 20;
- `metrics_enabled`.

**Credenciales AWS:** ninguna variable las contiene. boto3 usa su cadena por defecto: rol IAM o
perfil.

## 4. Recursos AWS creados (cuenta de desarrollo, us-east-1)

Creados con `infra/aws/provision.sh`, con aprobación explícita. La configuración se comprobó con la
CLI después de crearlos:

| Recurso | Configuración verificada |
|---|---|
| CMK KMS (`alias/translogix-dev-attachments`) | `CUSTOMER`, `Enabled`, rotación activa |
| Bucket `translogix-dev-attachments-e75b79` | Block Public Access 4/4, `BucketOwnerEnforced`, `IsPublic=false`, SSE-KMS por defecto con la CMK y Bucket Key, SSE-C bloqueado. La política niega peticiones sin TLS y subidas sin `aws:kms` o con otra clave |
| `translogix-dev-rag-jobs` | Standard, SSE-SQS, visibilidad 300 s, *long polling* 20 s, *redrive* a la DLQ con `maxReceiveCount=3` |
| `translogix-dev-rag-jobs-dlq` | SSE-SQS, retención 14 días, `RedriveAllowPolicy` solo desde la cola principal |
| Log group `/translogix/dev` | Retención 7 días |

**Identificadores:** `var/aws-dev-resources.env`, ignorado por Git y con permisos 600.

**Costo estimado:** ~1 USD/mes por la CMK más centavos de S3, SQS y Logs. Hay 3 métricas
personalizadas de evidencia (`TransLogixDevEvidence`) a ~0,30 USD/mes prorrateado.

**Para borrar todo:** `bash infra/aws/teardown.sh`.

## 5. Política IAM mínima

Plantillas en `infra/aws/`:

| Rol | Permisos |
|---|---|
| **API** (`iam-policy-api.json`) | `s3:PutObject`, `GetObject` y `DeleteObject` solo sobre `attachments/*`. `s3:ListBucket` limitado a ese prefijo (para que un objeto inexistente dé 404 y no 403). `kms:GenerateDataKey` y `kms:Decrypt` solo con `kms:ViaService=s3.<región>`. `sqs:SendMessage` y `GetQueueAttributes` sobre la cola |
| **Worker** (`iam-policy-worker.json`) | `sqs:ReceiveMessage`, `DeleteMessage`, `ChangeMessageVisibility`, `GetQueueAttributes` y `SendMessage` sobre la cola; `ReceiveMessage`, `DeleteMessage` y `GetQueueAttributes` sobre la DLQ. Nada de S3 |
| **Recolector de logs** | Rol del agente o de ECS (`logs:CreateLogStream`, `PutLogEvents`) |

La aplicación no llama a CloudWatch.

> **Limitación:** las pruebas reales se ejecutaron con el usuario IAM del desarrollador (perfil
> `default`), no con estos roles. Las políticas son correctas en su diseño, pero no se probaron con
> roles dedicados. No se crearon usuarios ni claves de acceso nuevos.

## 6. Migraciones y estrategia de *rollback*

**`0008_aws_storage_jobs`** (aditiva, después de 0007 tras revisar la cadena):
- columna `attachments.storage_backend` (`local` por defecto);
- tablas `attachment_storage_migrations`, `processing_jobs` y `outbox_messages`, con el índice
  parcial de pendientes.

**Evidencia en `translogix_test`:**

| Paso | Resultado |
|---|---|
| Downgrade con 3 adjuntos en S3 | **Rechazado**: "3 adjuntos están en S3…"; la base sigue en 0008 |
| Downgrade sin adjuntos en S3 | 0008 → 0007 elimina las 3 tablas |
| Upgrade | Vuelve a 0008 |
| `alembic check` | Sin diferencias |

**Rollback operativo de adjuntos:**
- los originales locales se conservan hasta `verify` + `cleanup --confirm`;
- con `ATTACHMENTS_STORAGE=local`, las filas `local` siguen sirviéndose desde disco, y una fila `s3`
  se sirve desde S3 mientras exista;
- no hay borrado automático ni se cambia la política legal de retención.

## 7. Pruebas y resultados

| Verificación | Resultado |
|---|---|
| `pytest` completo, sin credenciales ni variables AWS | **642 aprobadas, 6 omitidas** (las reales). Antes: 610 |
| Cobertura | 80 % total. `storage` 90 %, `metrics` 100 %, `jobs/outbox` 100 %, `jobs/worker` 91 %, `storage/migration` 92 %. **`app/storage/cli.py` y `app/worker/__main__.py` al 0 % en pytest**; el worker se ejerció de punta a punta en la prueba real |
| Fase 1, S3 (moto): `test_storage.py`, 11 | Adaptador local (atómico, idempotente, nunca sobrescribe), claves externas rechazadas (`../`, rutas absolutas, extensiones), adaptador S3 (SSE-KMS, metadatos, reintento sin duplicar, conflicto, URL SigV4) |
| Fase 1, S3 por la API (moto) | Subida y descarga por S3; propietario 200; otro usuario y clave arbitraria 404; anónimo 401; ADMIN 200; REVIEWER sin caso 404; API key 403; compensación (objeto borrado si falla la fila); bucket caído → 503 `ALMACENAMIENTO_NO_DISPONIBLE`; local sirve `/content` pero no emite `/link`; migración con hash, reanudación, `SOURCE_HASH_MISMATCH` y `SOURCE_MISSING`, originales conservados; logs sin PII |
| Fase 2, SQS (moto): `test_jobs.py`, 11 | Outbox atómico solo con ids; publicación fallida → pendiente y reintentada; duplicado procesado una sola vez; Ollama caído → `retry`, `retry`, `dead_letter` y luego DLQ, `sweep-dlq` y `retry`; recuperación tras un fallo; mensaje inválido, trabajo ocupado y *lease* vencido; API asíncrona 202 → DONE (REVIEWER 403); sin backend → 503; reconstrucción del BM25 entre procesos; errores y métricas sin PII |
| Fase 3, CloudWatch: `test_metrics.py`, 4 | Formato EMF válido; dimensiones con PII descartadas; `timer` con resultado; métricas desactivables; campos de log redactados |
| `ruff` | 19 E501 heredados; ninguno nuevo |
| `mypy` estricto (`app` y `tests`, 187 archivos) | Limpio |
| Contrato | `python -m app.contracts --check` al día (2.1.0); tipos TS regenerados |

**Fallos reales que encontraron las pruebas y ya están corregidos:**

| Fallo | Corrección |
|---|---|
| La URL prefirmada salía firmada con **SigV2**, que no sirve con SSE-KMS | Cliente S3 forzado a SigV4, con reintentos acotados |
| El último intento dejaba el mensaje invisible y retrasaba su paso a la DLQ | En el último intento se hace visible al instante |
| El *handler* EMF retenía un `stdout` viejo | Ahora resuelve `sys.stdout` en cada escritura |
| Los campos estructurados del worker no salían en el log | Se ampliaron los campos permitidos, que siguen redactados |

**Limitación de moto:** no valida `ChecksumSHA256`. Esa comprobación está en la prueba real.

## 8. Evidencia de integración real con AWS

Todo contra los recursos de §4, con credenciales del perfil y la cuenta oculta en las salidas:

| Prueba real | Resultado |
|---|---|
| `test_real_aws.py` | **6/6 aprobadas** (5 en 13 s y la de CloudWatch en 12 s) |
| S3 + KMS | `ServerSideEncryption=aws:kms` con la **CMK** en `SSEKMSKeyId`; `sha256` en metadatos; reintento idempotente; conflicto detectado |
| URL prefirmada | `GET` → 200 con los mismos bytes (SigV4 y descifrado KMS reales) |
| GET sin firma al objeto | **403** (bucket privado) |
| Checksum incorrecto | Rechazado por S3 |
| `PutObject` sin SSE-KMS, saltándose la app | **AccessDenied** por la política del bucket |
| SQS + DLQ | Cola real: `retry`, `retry`, `dead_letter`; SQS movió el mensaje a la DLQ real; `sweep-dlq` lo registró y el trabajo quedó en `DEAD_LETTER` |
| Worker de punta a punta | Trabajo en PostgreSQL → `python -m app.worker once` (proceso real) → SQS real → reindexación de `DOC-PQRS-2026-04` con **Ollama local** → `DONE` con *chunks* > 0 |
| API sobre S3 real (`ATTACHMENTS_STORAGE=s3`) | Subida 201 (`backend=s3`, clave `attachments/…`, SSE-KMS con la CMK); `/content` devuelve los mismos bytes; `/link` 200 y la URL descarga lo mismo; otro usuario 404 |
| Migración real (`python -m app.storage.cli`) | `copy`: 2 copiados y 1 `SOURCE_HASH_MISMATCH`; segunda `copy`: solo procesó el fallido (no duplica); `verify`: 2 VERIFIED; `cleanup` sin `--confirm`: 0 borrados; `cleanup --confirm`: 2 borrados; el corrupto quedó intacto en disco |
| CloudWatch | Línea EMF enviada al log group real con `PutLogEvents` (simula el recolector) → **dato extraído por CloudWatch** (`GetMetricStatistics`, Sum = 1.0 para la serie única de esa ejecución) en unos 11 s |

## 9. Riesgos pendientes

| Riesgo | Detalle y acción |
|---|---|
| IAM de mínimo privilegio sin probar con roles | Las pruebas usaron el usuario del desarrollador. Hay que crear los roles con `infra/aws/iam-policy-*.json` y repetir `-m aws` con ellos |
| No hay cómputo en AWS | La API y el worker corren en el portátil. La recolección de logs por un agente no está configurada: la evidencia de CloudWatch simula el recolector con `PutLogEvents` |
| Sin alarmas | Recomendadas: DLQ `ApproximateNumberOfMessagesVisible > 0`, `InfrastructureErrors > 0` y p95 de `OllamaLatency`. Faltan las alarmas y su destino (SNS/correo) |
| Adjuntos en disco sin cifrar | Mientras `ATTACHMENTS_STORAGE=local`, riesgo ya registrado en 2.0.0 |
| 64 archivos huérfanos en `var/attachments` | Sin fila: no se migran ni se borran. Su eliminación es una decisión de retención |
| Versionado del bucket desactivado | Recomendado en producción junto con una regla de ciclo de vida |
| Un solo flujo SQS | Solo la reindexación RAG. Si el worker no corre, los trabajos quedan PENDING, visibles en `/admin/rag/jobs/{id}` |
| Capacidad | SQS no añade inferencia: Ollama sigue siendo el límite (~0,5 respuestas/s) |
| `:8000` en 1.7.0 / BD de dev en 0005 | Desplegar 2.x exige coordinar con el front (2.0.0 es MAJOR), generar la clave PII y aplicar 0006 a 0008. Las variables AWS son opcionales |
| Recursos AWS activos | Cuestan (~1 USD/mes por la CMK). `infra/aws/teardown.sh` los borra cuando ya no se necesiten |

## 10. Los flujos originales siguen funcionando

Con la configuración por defecto (`ATTACHMENTS_STORAGE=local`, `JOBS_BACKEND=disabled` y sin
credenciales):

| Verificación | Resultado |
|---|---|
| Suite completa | 642 aprobadas, incluidas las 610 anteriores sin cambios de comportamiento: rastreo, cotización con la fórmula oficial, RAG, escalamiento, RBAC, propiedad, mercancía peligrosa, cifrado y limitador |
| Golden set RAG (Ollama real, contrato 2.1.0) | **18/18**, 0 alucinaciones |
| Sondas | 23 PASS / 3 FAIL / 1 BLOCKED, idéntico a 2.0.0 (los 3 FAIL son los conocidos: SEC-03, RAG-02 y TRK-02) |
| Saturación con 24 peticiones RAG simultáneas | 0 errores (12 citadas y 12 degradadas) |
| Contrato | Aditivo (MINOR). Ninguna operación de 2.0.0 cambió de forma; la reindexación síncrona responde igual que antes |
