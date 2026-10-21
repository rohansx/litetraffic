#!/bin/sh
# Usage: sh run.sh <scenario-dir> [extra verify args]. Logs in as the shop's admin (needed by setup and the
# final checks only; shoppers are anonymous), picks a price no other order has, then runs LiteTraffic.
# A copy of the bundle is made per run because the "paid orders" check must name this run's total, and an
# observation path cannot yet refer to the run's fixture (product gap, see LOG.md).
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
LT=${LITETRAFFIC:-litetraffic}
TOK=$(curl -s -i -X POST http://localhost:3000/admin-api -H 'Content-Type: application/json' \
  -d '{"query":"mutation{ login(username:\"superadmin\", password:\"superadmin\"){ ... on CurrentUser{ id } } }"}' \
  | grep -i '^vendure-auth-token' | awk '{print $2}' | tr -d '\r')
[ -n "$TOK" ] || { echo "admin login failed" >&2; exit 1; }
export LT_VENDURE_AUTH="Bearer $TOK"
sc=$1; shift
# price (cents, multiple of 5 so 20% tax is exact) and the resulting order total with 5.00 shipping
LT_PRICE=$(python3 -c "import time;print(100000+(int(time.time())%160000)*5)")
export LT_PRICE
TOTAL=$(python3 -c "import os;print(int(os.environ['LT_PRICE'])*6//5+500)")
STAGE=$(mktemp -d)/$sc; cp -R "$HERE/scenarios/$sc" "$STAGE"
PATH_Q=$(python3 - "$TOTAL" <<'PY'
import sys,urllib.parse
q='{orders(options:{filter:{totalWithTax:{eq:%s},state:{in:["PaymentAuthorized","PaymentSettled"]}}}){totalItems}}' % sys.argv[1]
print("/admin-api?query="+urllib.parse.quote(q,safe=""))
PY
)
sed -i.bak -e "s|/__PAID_ORDERS_PATH__|$PATH_Q|" -e "s|__PAID_ORDERS_PATH__|$PATH_Q|" "$STAGE/manifest.json" && rm "$STAGE/manifest.json.bak"
exec $LT verify "$STAGE" --target http://localhost:3000 --output-dir "$HERE/runs" "$@"
