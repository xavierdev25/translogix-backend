#!/usr/bin/env bash
# Creates the TransLogix AWS dev resources (CR-011). Idempotent where AWS allows it.
# Writes their identifiers (not credentials) to var/aws-dev-resources.env, which is gitignored.
# Credentials: those of the active profile/role (never written anywhere).
set -euo pipefail
REGION="${AWS_REGION:-us-east-1}"
PREFIX="${PREFIX:-translogix-dev}"
OUT="$(cd "$(dirname "$0")/../.." && pwd)/var/aws-dev-resources.env"
[ -f "$OUT" ] && source "$OUT"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
BUCKET="${S3_BUCKET:-$PREFIX-attachments-$(openssl rand -hex 3)}"

# 1. Customer-managed KMS key (automatic yearly rotation) and alias.
if [ -z "${S3_KMS_KEY_ID:-}" ]; then
  S3_KMS_KEY_ID="$(aws kms create-key --region "$REGION" --description "TransLogix dev: SSE-KMS de adjuntos" \
    --tags TagKey=project,TagValue=translogix TagKey=env,TagValue=dev --query KeyMetadata.Arn --output text)"
  aws kms enable-key-rotation --region "$REGION" --key-id "$S3_KMS_KEY_ID"
  aws kms create-alias --region "$REGION" --alias-name "alias/$PREFIX-attachments" --target-key-id "$S3_KMS_KEY_ID"
fi

# 2. Private bucket: Block Public Access, no ACLs, default SSE-KMS (Bucket Key), restrictive policy.
if ! aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  aws s3api create-bucket --region "$REGION" --bucket "$BUCKET" >/dev/null
fi
aws s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-ownership-controls --bucket "$BUCKET" \
  --ownership-controls 'Rules=[{ObjectOwnership=BucketOwnerEnforced}]'
aws s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
  "{\"Rules\":[{\"ApplyServerSideEncryptionByDefault\":{\"SSEAlgorithm\":\"aws:kms\",\"KMSMasterKeyID\":\"$S3_KMS_KEY_ID\"},\"BucketKeyEnabled\":true}]}"
POLICY="$(sed -e "s#\${BUCKET}#$BUCKET#g" -e "s#\${KMS_KEY_ARN}#$S3_KMS_KEY_ID#g" "$(dirname "$0")/s3-bucket-policy.json")"
aws s3api put-bucket-policy --bucket "$BUCKET" --policy "$POLICY"
aws s3api put-bucket-tagging --bucket "$BUCKET" --tagging 'TagSet=[{Key=project,Value=translogix},{Key=env,Value=dev}]'

# 3. DLQ and Standard queue (SSE-SQS, long polling, redrive after 3 receives).
DLQ_URL="$(aws sqs create-queue --region "$REGION" --queue-name "$PREFIX-rag-jobs-dlq" \
  --attributes SqsManagedSseEnabled=true,MessageRetentionPeriod=1209600 --query QueueUrl --output text)"
DLQ_ARN="$(aws sqs get-queue-attributes --region "$REGION" --queue-url "$DLQ_URL" --attribute-names QueueArn --query Attributes.QueueArn --output text)"
QUEUE_URL="$(aws sqs create-queue --region "$REGION" --queue-name "$PREFIX-rag-jobs" --attributes \
  "{\"SqsManagedSseEnabled\":\"true\",\"VisibilityTimeout\":\"300\",\"ReceiveMessageWaitTimeSeconds\":\"20\",\"RedrivePolicy\":\"{\\\"deadLetterTargetArn\\\":\\\"$DLQ_ARN\\\",\\\"maxReceiveCount\\\":\\\"3\\\"}\"}" \
  --query QueueUrl --output text)"
QUEUE_ARN="$(aws sqs get-queue-attributes --region "$REGION" --queue-url "$QUEUE_URL" --attribute-names QueueArn --query Attributes.QueueArn --output text)"
aws sqs set-queue-attributes --region "$REGION" --queue-url "$DLQ_URL" --attributes \
  "{\"RedriveAllowPolicy\":\"{\\\"redrivePermission\\\":\\\"byQueue\\\",\\\"sourceQueueArns\\\":[\\\"$QUEUE_ARN\\\"]}\"}"

# 4. Log group for the EMF metrics (7 days: operational logs without PII).
aws logs create-log-group --region "$REGION" --log-group-name "/translogix/dev" 2>/dev/null || true
aws logs put-retention-policy --region "$REGION" --log-group-name "/translogix/dev" --retention-in-days 7

mkdir -p "$(dirname "$OUT")"
cat > "$OUT" <<ENV
AWS_REGION=$REGION
S3_BUCKET=$BUCKET
S3_KMS_KEY_ID=$S3_KMS_KEY_ID
SQS_QUEUE_URL=$QUEUE_URL
SQS_DLQ_URL=$DLQ_URL
QUEUE_ARN=$QUEUE_ARN
DLQ_ARN=$DLQ_ARN
LOG_GROUP=/translogix/dev
ENV
chmod 600 "$OUT"
echo "Recursos listos (identificadores en $OUT). Cuenta: ${ACCOUNT:0:4}********"
