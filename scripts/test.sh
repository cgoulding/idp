#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<EOF
Usage:
  ./scripts/test.sh \\
    --base-url https://xxxxx.cloudfront.net \\
    --jwt 'eyJ...' \\
    --api-key 'YOUR_KEY' \\
    --file /path/to/doc.pdf \\
    --file-type pdf|image \\
    [--content-type application/pdf|image/png|image/jpeg] \\
    [--prompt "Summarize in 5 bullets"]

Notes:
- For PDFs: flow is upload-url -> PUT -> invoke -> poll status until SUCCEEDED
- For images: upload-url -> PUT -> invoke (sync)
EOF
}

BASE_URL=""
JWT=""
API_KEY=""
FILE=""
FILE_TYPE=""
CONTENT_TYPE=""
PROMPT="Summarize this document in 5 bullet points and list key risks."

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-url) BASE_URL="$2"; shift 2 ;;
    --jwt) JWT="$2"; shift 2 ;;
    --api-key) API_KEY="$2"; shift 2 ;;
    --file) FILE="$2"; shift 2 ;;
    --file-type) FILE_TYPE="$2"; shift 2 ;;
    --content-type) CONTENT_TYPE="$2"; shift 2 ;;
    --prompt) PROMPT="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1"; usage; exit 1 ;;
  esac
done

if [[ -z "$BASE_URL" || -z "$JWT" || -z "$API_KEY" || -z "$FILE" || -z "$FILE_TYPE" ]]; then
  echo "Missing required args."
  usage
  exit 1
fi

if [[ -z "$CONTENT_TYPE" ]]; then
  if [[ "$FILE_TYPE" == "pdf" ]]; then
    CONTENT_TYPE="application/pdf"
  else
    # best-effort default
    CONTENT_TYPE="image/png"
  fi
fi

echo "1) Requesting upload URL..."
UPLOAD_JSON="$(curl -sS -X POST "${BASE_URL}/upload-url" \
  -H "Authorization: Bearer ${JWT}" \
  -H "Content-Type: application/json" \
  -d "{\"content_type\":\"${CONTENT_TYPE}\"}")"
echo "$UPLOAD_JSON"

UPLOAD_URL="$(echo "$UPLOAD_JSON" | python3 -c 'import sys,json;print(json.load(sys.stdin)["upload_url"])')"
S3_KEY="$(echo "$UPLOAD_JSON" | python3 -c 'import sys,json;print(json.load(sys.stdin)["s3_key"])')"

echo "   s3_key: $S3_KEY"
echo "2) Uploading file to S3 via presigned URL..."
curl -sS -X PUT "$UPLOAD_URL" \
  -H "Content-Type: ${CONTENT_TYPE}" \
  --data-binary "@${FILE}" >/dev/null

echo "3) Calling /invoke..."
echo "url: ${BASE_URL}/invoke"
echo "x-api-key: ${API_KEY}"
echo "{\"s3_key\":\"${S3_KEY}\",\"file_type\":\"${FILE_TYPE}\",\"prompt\":\"${PROMPT}\"}"

INVOKE_JSON="$(curl -sS -X POST "${BASE_URL}/invoke" \
  -H "Authorization: Bearer ${JWT}" \
  -H "x-api-key: ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d "{\"s3_key\":\"${S3_KEY}\",\"file_type\":\"${FILE_TYPE}\",\"prompt\":\"${PROMPT}\"}")"

echo "$INVOKE_JSON" | python3 -m json.tool >/dev/null || true

STATUS_FIELD="$(echo "$INVOKE_JSON" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("statusCode",""))' 2>/dev/null || true)"

# For image, invoke returns 200 with response (sync).
# For pdf, invoke returns 202 with job_id.
JOB_ID="$(echo "$INVOKE_JSON" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("job_id",""))')"

if [[ -z "$JOB_ID" ]]; then
  echo "Invoke returned sync result (likely image). Response:"
  echo "$INVOKE_JSON" | python3 -m json.tool
  exit 0
fi

echo "   job_id: $JOB_ID"
echo "4) Polling /status until SUCCEEDED (this can take a bit)..."

for i in $(seq 1 60); do
  STATUS_JSON="$(curl -sS -X POST "${BASE_URL}/status" \
    -H "Authorization: Bearer ${JWT}" \
    -H "x-api-key: ${API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"job_id\":\"${JOB_ID}\",\"prompt\":\"${PROMPT}\",\"run_bedrock\":true}")"

  STATUS="$(echo "$STATUS_JSON" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("status",""))')"
  echo "   [$i] status=$STATUS"

  if [[ "$STATUS" == "SUCCEEDED" ]]; then
    echo "✅ Done. Final response:"
    echo "$STATUS_JSON" | python3 -m json.tool
    exit 0
  fi

  sleep 3
done

echo "❌ Timed out waiting for SUCCEEDED. Last status payload:"
echo "$STATUS_JSON" | python3 -m json.tool
exit 1