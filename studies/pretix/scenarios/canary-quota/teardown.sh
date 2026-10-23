#!/bin/sh
# Leave the event as found: remove this run's test-mode orders so the next run starts clean.
set -eu
[ -n "${LT_PRETIX_AUTH:-}" ] || exit 0
API="$LT_TARGET/api/v1/organizers/study/events/lab"
for code in $(curl -sf -H "Authorization: $LT_PRETIX_AUTH" "$API/orders/?page_size=200" | python3 -c "import sys,json;[print(o['code']) for o in json.load(sys.stdin)['results']]" 2>/dev/null || true); do
  curl -s -o /dev/null -X DELETE -H "Authorization: $LT_PRETIX_AUTH" "$API/orders/$code/" || true
done
