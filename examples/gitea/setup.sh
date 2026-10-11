#!/bin/sh
# Fresh private repo for this run; the final observation counts its open issues.
set -eu
A="$LT_TARGET/api/v1"
auth="Authorization: token $GITEA_TOKEN_ALICE"
curl -sf -o /dev/null -X DELETE -H "$auth" "$A/repos/alice/lt-issues" || true
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "$auth" -H 'Content-Type: application/json' \
  "$A/user/repos" -d '{"name":"lt-issues","private":true,"auto_init":true,"description":"litetraffic run '"$LT_RUN_ID"'"}')
[ "$code" = 201 ] || { echo "create repo: HTTP $code" >&2; exit 1; }
printf '{"owner":"alice","repo":"lt-issues"}\n'
