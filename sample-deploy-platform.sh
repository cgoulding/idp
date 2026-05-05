./scripts/deploy-platform.sh \
  --vpc-id vpc-088da6032c7d63fe4 \
  --private-subnets subnet-0d73ac479e1b62332,subnet-0e9f1f89b09a318e5 \
  --private-route-tables rtb-06c897e20dfa230ff \
  --seed-api-key "sk_test_dev_idp" \
  --name-prefix idp \
  --region eu-west-1 \
  --seed-tier pro \
  --cors-origins "*"