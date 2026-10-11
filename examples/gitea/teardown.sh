#!/bin/sh
set -eu
curl -s -o /dev/null -w 'delete repo: HTTP %{http_code}\n' -X DELETE -H "Authorization: token $GITEA_TOKEN_ALICE" "$LT_TARGET/api/v1/repos/alice/lt-issues"
