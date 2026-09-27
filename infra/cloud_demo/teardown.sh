#!/usr/bin/env bash
# Remove the read-only cloud demo (D-067): HTTP API, Lambda (+ log group), s3://fai-tce-team49-data/cloud-demo/,
# and the bucket itself if nothing else is in it. Asks for confirmation (irreversible).
set -euo pipefail
cd "$(dirname "$0")/../.."
P="--profile fai-builder --region ap-south-1"
BUCKET=fai-tce-team49-data; PREFIX=cloud-demo; FN=fai-tce-team49-api; API_NAME=fai-tce-team49-api
read -r -p "Delete the cloud demo (API $API_NAME, Lambda $FN, s3://$BUCKET/$PREFIX/)? Type 'delete': " ok
[ "$ok" = "delete" ] || { echo "cancelled"; exit 1; }
now() { date -u +"%Y-%m-%d %H:%M UTC"; }
API_ID=$(aws apigatewayv2 get-apis $P --query "Items[?Name=='$API_NAME'].ApiId | [0]" --output text)
[ "$API_ID" != "None" ] && [ -n "$API_ID" ] && aws apigatewayv2 delete-api --api-id "$API_ID" $P && echo "deleted API $API_ID"
aws lambda delete-function --function-name "$FN" $P 2>/dev/null && echo "deleted Lambda $FN" || true
aws logs delete-log-group --log-group-name "/aws/lambda/$FN" $P 2>/dev/null && echo "deleted log group" || true
aws s3 rm "s3://$BUCKET/$PREFIX/" --recursive $P --only-show-errors && echo "emptied s3://$BUCKET/$PREFIX/"
if [ -z "$(aws s3 ls "s3://$BUCKET/" $P)" ]; then aws s3api delete-bucket --bucket "$BUCKET" $P && echo "deleted bucket $BUCKET"; fi
echo "| (teardown) | cloud demo API/Lambda/log group/cloud-demo prefix | — | $(now) | infra/cloud_demo/teardown.sh |" >> infra/RESOURCES.md
