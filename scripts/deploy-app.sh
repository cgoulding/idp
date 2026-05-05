#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<EOF
Usage:
  ./scripts/deploy-app.sh \
    --platform-state infra/platform/terraform.tfstate \
    [--bedrock-model-id anthropic.claude-3-5-sonnet-20240620-v1:0] \
    [--monthly-free 50000] \
    [--monthly-pro 1000000]
EOF
}

PLATFORM_STATE=""
MODEL_ID="anthropic.claude-3-5-sonnet-20240620-v1:0"
MONTHLY_FREE="50000"
MONTHLY_PRO="1000000"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform-state) PLATFORM_STATE="$2"; shift 2 ;;
    --bedrock-model-id) MODEL_ID="$2"; shift 2 ;;
    --monthly-free) MONTHLY_FREE="$2"; shift 2 ;;
    --monthly-pro) MONTHLY_PRO="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1"; usage; exit 1 ;;
  esac
done

if [[ -z "$PLATFORM_STATE" ]]; then
  echo "Missing --platform-state"
  usage
  exit 1
fi

cat > infra/app/terraform.tfvars <<EOF
platform_state_path = "${PLATFORM_STATE}"
bedrock_model_id = "${MODEL_ID}"
monthly_token_limit_free = ${MONTHLY_FREE}
monthly_token_limit_pro  = ${MONTHLY_PRO}
EOF

pushd infra/app >/dev/null
terraform init
terraform apply -auto-approve
echo ""
echo "App outputs:"
terraform output
popd >/dev/null
