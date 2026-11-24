#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<EOF
Usage:
  ./scripts/destroy-platform.sh [--force]

Notes:
  - Destroys the shared platform stack in infra/platform.
  - Refuses to run while the app stack still has resources in state unless --force is used.
  - Empties the managed uploads bucket before terraform destroy.
EOF
}

FORCE="false"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --force) FORCE="true"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1"; usage; exit 1 ;;
  esac
done

if [[ ! -f infra/platform/terraform.tfvars ]]; then
  echo "Missing infra/platform/terraform.tfvars. Deploy the platform first or restore the tfvars file."
  exit 1
fi

if [[ "$FORCE" != "true" ]] && [[ -f infra/app/terraform.tfstate ]]; then
  APP_RESOURCES="$(terraform -chdir=infra/app state list 2>/dev/null || true)"
  if [[ -n "$APP_RESOURCES" ]]; then
    echo "App resources still exist in infra/app state. Destroy the app stack first or rerun with --force."
    exit 1
  fi
fi

pushd infra/platform >/dev/null
terraform init

UPLOAD_BUCKET="$(terraform output -raw upload_bucket 2>/dev/null || true)"
AWS_REGION="$(terraform output -raw aws_region 2>/dev/null || true)"

if [[ -n "$UPLOAD_BUCKET" ]]; then
  if ! command -v aws >/dev/null 2>&1; then
    echo "AWS CLI is required to empty s3://${UPLOAD_BUCKET} before destroy."
    exit 1
  fi

  echo "Emptying s3://${UPLOAD_BUCKET}"
  if [[ -n "$AWS_REGION" ]]; then
    aws --region "$AWS_REGION" s3 rm "s3://${UPLOAD_BUCKET}" --recursive >/dev/null
  else
    aws s3 rm "s3://${UPLOAD_BUCKET}" --recursive >/dev/null
  fi
fi

terraform destroy -auto-approve
popd >/dev/null
