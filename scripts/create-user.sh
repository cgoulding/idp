#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<EOF
Usage:
  ./scripts/create-user.sh \
    --username <EMAIL_OR_USERNAME> \
    --password <PASSWORD> \
    --tenant-id <TENANT_ID> \
    [--region eu-west-1] \
    [--platform-dir infra/platform]

It reads cognito_user_pool_id and cognito_app_client_id from Terraform outputs.
EOF
}

REGION="eu-west-1"
PLATFORM_DIR="infra/platform"
USERNAME=""
PASSWORD=""
TENANT_ID=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --region) REGION="$2"; shift 2 ;;
    --platform-dir) PLATFORM_DIR="$2"; shift 2 ;;
    --username) USERNAME="$2"; shift 2 ;;
    --password) PASSWORD="$2"; shift 2 ;;
    --tenant-id) TENANT_ID="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1"; usage; exit 1 ;;
  esac
done

if [[ -z "$USERNAME" || -z "$PASSWORD" || -z "$TENANT_ID" ]]; then
  echo "Missing required args."
  usage
  exit 1
fi

POOL_ID="$(cd "$PLATFORM_DIR" && terraform output -raw cognito_user_pool_id)"
CLIENT_ID="$(cd "$PLATFORM_DIR" && terraform output -raw cognito_app_client_id)"

# Create/update user
set +e
aws cognito-idp admin-get-user --region "$REGION" --user-pool-id "$POOL_ID" --username "$USERNAME" >/dev/null 2>&1
EXISTS=$?
set -e

if [[ $EXISTS -ne 0 ]]; then
  aws cognito-idp admin-create-user \
    --region "$REGION" \
    --user-pool-id "$POOL_ID" \
    --username "$USERNAME" \
    --message-action SUPPRESS \
    --user-attributes \
      Name="email",Value="$USERNAME" \
      Name="email_verified",Value="true" \
      Name="custom:tenant_id",Value="$TENANT_ID" \
    >/dev/null
else
  aws cognito-idp admin-update-user-attributes \
    --region "$REGION" \
    --user-pool-id "$POOL_ID" \
    --username "$USERNAME" \
    --user-attributes Name="custom:tenant_id",Value="$TENANT_ID" \
    >/dev/null
fi

aws cognito-idp admin-set-user-password \
  --region "$REGION" \
  --user-pool-id "$POOL_ID" \
  --username "$USERNAME" \
  --password "$PASSWORD" \
  --permanent >/dev/null

TOKENS_JSON="$(aws cognito-idp initiate-auth \
  --region "$REGION" \
  --client-id "$CLIENT_ID" \
  --auth-flow USER_PASSWORD_AUTH \
  --auth-parameters USERNAME="$USERNAME",PASSWORD="$PASSWORD")"

ID_TOKEN="$(echo "$TOKENS_JSON" | python3 -c 'import sys,json;print(json.load(sys.stdin)["AuthenticationResult"]["IdToken"])')"
echo "$ID_TOKEN"
