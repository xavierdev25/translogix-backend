#!/usr/bin/env bash
# Deletes the TransLogix AWS dev resources. **It deletes every object in the bucket**: migrate
# them back or keep a copy first. The KMS key is scheduled for deletion in 7 days (the minimum),
# which can still be cancelled with `aws kms cancel-key-deletion`.
set -euo pipefail
OUT="$(cd "$(dirname "$0")/../.." && pwd)/var/aws-dev-resources.env"
source "$OUT"
read -r -p "Esto borra el bucket $S3_BUCKET y sus objetos, las colas y el log group. Escribe 'borrar': " ok
[ "$ok" = "borrar" ] || { echo "Cancelado."; exit 1; }
aws s3 rm "s3://$S3_BUCKET" --recursive
aws s3api delete-bucket --bucket "$S3_BUCKET"
aws sqs delete-queue --region "$AWS_REGION" --queue-url "$SQS_QUEUE_URL"
aws sqs delete-queue --region "$AWS_REGION" --queue-url "$SQS_DLQ_URL"
aws logs delete-log-group --region "$AWS_REGION" --log-group-name "$LOG_GROUP"
aws kms delete-alias --region "$AWS_REGION" --alias-name "alias/translogix-dev-attachments" || true
aws kms schedule-key-deletion --region "$AWS_REGION" --key-id "$S3_KMS_KEY_ID" --pending-window-in-days 7
rm -f "$OUT"
echo "Recursos eliminados (clave KMS programada para borrarse en 7 días)."
