#!/bin/sh
# Usage: sh run.sh <scenario-name> [extra verify args, e.g. --seed 7]
# Local Kill Bill only (127.0.0.1:8480). The admin login is the image's public default; the tenant keys are
# throwaway test values created here. A staged copy of the bundle is made per run because the observation
# must name this run's unique key (an observation path cannot yet refer to the run's fixture: product gap).
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
LT=${LITETRAFFIC:-litetraffic}
BASE=http://127.0.0.1:8480/1.0/kb
export LT_KB_AUTH="Basic $(printf 'admin:password' | base64)"
export LT_KB_KEY=lt-tenant-a LT_KB_SECRET=lt-local-secret-a
export LT_KB_KEY_B=lt-tenant-b LT_KB_SECRET_B=lt-local-secret-b
for t in a b; do   # idempotent: 201 on first run, an error status afterwards
  curl -s -o /dev/null -u admin:password -X POST "$BASE/tenants" -H 'Content-Type: application/json' \
    -H 'X-Killbill-CreatedBy: lt' -d "{\"apiKey\":\"lt-tenant-$t\",\"apiSecret\":\"lt-local-secret-$t\"}" || true
done
sc=$1; shift
LT_KEY="lt$(date -u +%H%M%S)$(python3 -c 'import secrets;print(secrets.token_hex(3))')"
export LT_KEY
STAGE=$(mktemp -d)/$sc
cp -R "$HERE/scenarios/$sc" "$STAGE"
sed -i.bak "s|__KEY__|$LT_KEY|g" "$STAGE/manifest.json" && rm "$STAGE/manifest.json.bak"
exec $LT verify "$STAGE" --target http://127.0.0.1:8480 --output-dir "$HERE/runs" "$@"
