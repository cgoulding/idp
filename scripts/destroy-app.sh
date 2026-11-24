#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<EOF
Usage:
  ./scripts/destroy-app.sh \
    [--platform-state infra/platform/terraform.tfstate]
EOF
}

PLATFORM_STATE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --platform-state) PLATFORM_STATE="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1"; usage; exit 1 ;;
  esac
done

if [[ -n "$PLATFORM_STATE" ]]; then
  cat > infra/app/terraform.tfvars <<EOF
platform_state_path = "${PLATFORM_STATE}"
EOF
elif [[ ! -f infra/app/terraform.tfvars ]]; then
  echo "Missing infra/app/terraform.tfvars. Pass --platform-state or deploy the app first."
  exit 1
fi

pushd infra/app >/dev/null
terraform init
terraform destroy -auto-approve
popd >/dev/null
