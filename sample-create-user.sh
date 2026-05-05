JWT="$(./scripts/create-user-and-token.sh \
  --username "connor.goulding@gmail.com" \
  --password "MySecurePassw0rd!123" \
  --tenant-id "idp_dev" \
  --region eu-west-1)"
echo "$JWT"