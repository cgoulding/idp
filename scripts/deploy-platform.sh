#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<EOF
Usage:
  ./scripts/deploy-platform.sh \
    --vpc-id vpc-xxx \
    --private-subnets subnet-a,subnet-b \
    --private-route-tables rtb-a,rtb-b \
    --seed-api-key YOUR_KEY \
    [--name-prefix ai] \
    [--region eu-west-1] \
    [--seed-tier pro|free] \
    [--cors-origins "https://example.com,https://app.example.com|*"]
EOF
}

REGION="eu-west-1"
NAME_PREFIX="ai"
SEED_TIER="pro"
CORS_ORIGINS="*"

VPC_ID=""
PRIVATE_SUBNETS=""
PRIVATE_RTS=""
SEED_API_KEY=""

to_hcl_list() {
  local csv="$1"
  IFS=',' read -ra parts <<< "$csv"
  local out="["
  for p in "${parts[@]}"; do
    p="$(echo "$p" | xargs)"
    out="${out}\"${p}\","
  done
  out="${out%,}]"
  echo "$out"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --region) REGION="$2"; shift 2 ;;
    --name-prefix) NAME_PREFIX="$2"; shift 2 ;;
    --vpc-id) VPC_ID="$2"; shift 2 ;;
    --private-subnets) PRIVATE_SUBNETS="$2"; shift 2 ;;
    --private-route-tables) PRIVATE_RTS="$2"; shift 2 ;;
    --seed-api-key) SEED_API_KEY="$2"; shift 2 ;;
    --seed-tier) SEED_TIER="$2"; shift 2 ;;
    --cors-origins) CORS_ORIGINS="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1"; usage; exit 1 ;;
  esac
done

if [[ -z "$VPC_ID" || -z "$PRIVATE_SUBNETS" || -z "$PRIVATE_RTS" || -z "$SEED_API_KEY" ]]; then
  echo "Missing required args."
  usage
  exit 1
fi

SUBNETS_HCL="$(to_hcl_list "$PRIVATE_SUBNETS")"
RTS_HCL="$(to_hcl_list "$PRIVATE_RTS")"

if [[ "$CORS_ORIGINS" == "*" ]]; then
  CORS_HCL='["*"]'
else
  CORS_HCL="$(to_hcl_list "$CORS_ORIGINS")"
fi

cat > infra/platform/terraform.tfvars <<EOF
aws_region = "${REGION}"
name_prefix = "${NAME_PREFIX}"

vpc_id = "${VPC_ID}"
private_subnet_ids = ${SUBNETS_HCL}
private_route_table_ids = ${RTS_HCL}

seed_api_key_value = "${SEED_API_KEY}"
seed_api_key_tier  = "${SEED_TIER}"

cors_allowed_origins = ${CORS_HCL}
EOF

pushd infra/platform >/dev/null
terraform init
terraform apply -auto-approve
echo ""
echo "Platform outputs:"
terraform output
popd >/dev/null