JWT="$(./scripts/create-token.sh \
  --username "connor.goulding@gmail.com" \
  --password "MySecurePassw0rd!123" \
  --region eu-west-1)"
echo "$JWT"