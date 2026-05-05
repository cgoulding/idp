#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<EOF
Usage:
  ./scripts/create-token.sh \
    --username <EMAIL_OR_USERNAME> \
    --password <PASSWORD> \
    [--region eu-west-1] \
    [--platform-dir infra/platform]

It reads cognito_user_pool_id and cognito_app_client_id from Terraform outputs.
EOF
}

REGION="eu-west-1"
PLATFORM_DIR="infra/platform"
USERNAME=""
PASSWORD=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --region) REGION="$2"; shift 2 ;;
    --platform-dir) PLATFORM_DIR="$2"; shift 2 ;;
    --username) USERNAME="$2"; shift 2 ;;
    --password) PASSWORD="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1"; usage; exit 1 ;;
  esac
done

if [[ -z "$USERNAME" || -z "$PASSWORD" ]]; then
  echo "Missing required args."
  usage
  exit 1
fi

CLIENT_ID="$(cd "$PLATFORM_DIR" && terraform output -raw cognito_app_client_id)"
echo $CLIENT_ID

TOKENS_JSON="$(aws cognito-idp initiate-auth \
  --region "$REGION" \
  --client-id "$CLIENT_ID" \
  --auth-flow USER_PASSWORD_AUTH \
  --auth-parameters USERNAME="$USERNAME",PASSWORD="$PASSWORD")"

ID_TOKEN="$(echo "$TOKENS_JSON" | python3 -c 'import sys,json;print(json.load(sys.stdin)["AuthenticationResult"]["IdToken"])')"
echo "$ID_TOKEN"
