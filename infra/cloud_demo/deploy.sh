#!/usr/bin/env bash
# Deploy / update the read-only cloud demo (D-067). Idempotent: re-running syncs changed data and
# updates the Lambda code. Uses only approved resources (team 49, ap-south-1, existing Lambda role).
#   S3 bucket   fai-tce-team49-data (private)   prefix cloud-demo/{snapshot,pages,chips}
#   Lambda      fai-tce-team49-api              python3.12, FAI-TCE-LambdaExecutionRole
#   HTTP API    fai-tce-team49-api              $default route -> Lambda (public URL)
# Prereqs: make cloud-export && make cloud-package; AWS SSO session (make aws-login).
set -euo pipefail
cd "$(dirname "$0")/../.."
P="--profile fai-builder --region ap-south-1"
BUCKET=fai-tce-team49-data; PREFIX=cloud-demo; FN=fai-tce-team49-api; API_NAME=fai-tce-team49-api
ACCOUNT=163887963251; ROLE=arn:aws:iam::$ACCOUNT:role/FAI-TCE-LambdaExecutionRole
ZIP=dist/cloud/lambda.zip; RES=infra/RESOURCES.md
say() { printf '\n==> %s\n' "$*"; }
now() { date -u +"%Y-%m-%d %H:%M UTC"; }
[ -f "$ZIP" ] || { echo "missing $ZIP (run make cloud-package)"; exit 1; }

say "1/4 data -> s3://$BUCKET/$PREFIX/ (only changed files)"
aws s3 sync data/cloud/snapshot "s3://$BUCKET/$PREFIX/snapshot/" $P --only-show-errors --delete
aws s3 sync data/pages "s3://$BUCKET/$PREFIX/pages/" $P --only-show-errors
[ -d data/s2/chips ] && aws s3 sync data/s2/chips "s3://$BUCKET/$PREFIX/chips/" $P --only-show-errors

ENV="Variables={SNAPSHOT_URI=s3://$BUCKET/$PREFIX/snapshot,ASSET_BUCKET=$BUCKET,ASSET_PREFIX=$PREFIX}"
say "2/4 Lambda $FN"
if aws lambda get-function --function-name "$FN" $P >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$FN" --zip-file "fileb://$ZIP" $P --query LastModified --output text
  aws lambda wait function-updated --function-name "$FN" $P
  aws lambda update-function-configuration --function-name "$FN" --environment "$ENV" --timeout 29 --memory-size 1024 \
    --ephemeral-storage Size=1024 $P --query LastModified --output text
  aws lambda wait function-updated --function-name "$FN" $P
else
  aws lambda create-function --function-name "$FN" --runtime python3.12 --architectures x86_64 \
    --handler app.cloud.lambda_handler.handler --role "$ROLE" --zip-file "fileb://$ZIP" \
    --timeout 29 --memory-size 1024 --ephemeral-storage Size=1024 --environment "$ENV" \
    --description "NilaSaatchi AI read-only cloud demo (team 49, D-067)" $P --query FunctionArn --output text
  aws lambda wait function-active-v2 --function-name "$FN" $P
  echo "| Lambda function | \`$FN\` (\`arn:aws:lambda:ap-south-1:$ACCOUNT:function:$FN\`) | $(now) | — | python3.12 zip, role FAI-TCE-LambdaExecutionRole, 1024 MB, timeout 29 s, /tmp 1 GB; env SNAPSHOT_URI/ASSET_BUCKET/ASSET_PREFIX |" >> "$RES"
fi
FN_ARN=$(aws lambda get-function --function-name "$FN" $P --query Configuration.FunctionArn --output text)

say "3/4 HTTP API $API_NAME"
API_ID=$(aws apigatewayv2 get-apis $P --query "Items[?Name=='$API_NAME'].ApiId | [0]" --output text)
if [ "$API_ID" = "None" ] || [ -z "$API_ID" ]; then
  API_ID=$(aws apigatewayv2 create-api --name "$API_NAME" --protocol-type HTTP --target "$FN_ARN" \
    --description "NilaSaatchi AI read-only cloud demo (team 49)" $P --query ApiId --output text)
  aws lambda add-permission --function-name "$FN" --statement-id apigw-invoke --action lambda:InvokeFunction \
    --principal apigateway.amazonaws.com --source-arn "arn:aws:execute-api:ap-south-1:$ACCOUNT:$API_ID/*" $P >/dev/null
  echo "| HTTP API | \`$API_NAME\` (id \`$API_ID\`) | $(now) | — | quick-create: \$default route -> Lambda proxy, auto-deploy stage; public URL https://$API_ID.execute-api.ap-south-1.amazonaws.com/ |" >> "$RES"
fi
URL="https://$API_ID.execute-api.ap-south-1.amazonaws.com"

say "4/4 smoke test"
for path in /health /stats/overview /; do
  printf '%-18s %s\n' "$path" "$(curl -s -o /dev/null -w '%{http_code} %{time_total}s' "$URL$path")"
done
echo "bedrock-from-lambda: $(curl -s "$URL/health/bedrock")"
echo; echo "Cloud demo: $URL/"
