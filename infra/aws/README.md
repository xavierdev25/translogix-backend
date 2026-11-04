# AWS de TransLogix (desarrollo): S3, SQS y CloudWatch (CR-011)

PostgreSQL y Ollama siguen locales. Estos recursos son opcionales: sin las variables de
`.env.example` §2.1.0, el backend usa el disco y la reindexación síncrona, como en 2.0.0.

| Archivo | Para qué |
|---|---|
| `provision.sh` | Crea los recursos y escribe sus identificadores (no las credenciales) en `var/aws-dev-resources.env` (ignorado por Git) |
| `teardown.sh` | Borra todo, con confirmación. La clave KMS queda programada para borrarse en 7 días |
| `s3-bucket-policy.json` | Política del bucket: niega peticiones sin TLS y subidas sin SSE-KMS con la clave del proyecto |
| `iam-policy-api.json` | Rol de la API: objetos `attachments/*`, KMS solo vía S3, envío a la cola |
| `iam-policy-worker.json` | Rol del worker: consumir la cola y la DLQ |

**Recursos que crea `provision.sh`** (us-east-1, prefijo `translogix-dev`):

| Recurso | Configuración |
|---|---|
| Clave KMS | Gestionada por el cliente, rotación anual, alias `alias/translogix-dev-attachments` |
| Bucket `translogix-dev-attachments-<sufijo>` | Block Public Access completo, `BucketOwnerEnforced` (sin ACL), SSE-KMS por defecto con Bucket Key y política restrictiva |
| Cola `translogix-dev-rag-jobs` | Standard, SSE-SQS, visibilidad 300 s, *long polling* 20 s, *redrive* a la DLQ con `maxReceiveCount=3` |
| Cola `translogix-dev-rag-jobs-dlq` | SSE-SQS, retención 14 días, `RedriveAllowPolicy` solo desde la cola principal |
| Log group `/translogix/dev` | Retención 7 días, para las métricas EMF |

**Uso:**

```bash
AWS_REGION=us-east-1 bash infra/aws/provision.sh
set -a; source var/aws-dev-resources.env; set +a
cd backend && TRANSLOGIX_AWS_TESTS=1 uv run pytest -m aws tests/aws/test_real_aws.py
ATTACHMENTS_STORAGE=s3 JOBS_BACKEND=sqs uv run python -m app.worker run
bash infra/aws/teardown.sh   # cuando ya no se necesiten
```

**Políticas IAM:** las de este directorio son plantillas; los `${…}` se sustituyen con los valores
de `var/aws-dev-resources.env`. En producción, asígnenlas a roles (ECS task role, instance profile).
Nunca usen claves de acceso de larga duración ni las guarden en el repositorio.
