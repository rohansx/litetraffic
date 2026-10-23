#!/bin/sh
# Usage: kb.sh <tenantKey> <METHOD> <path> [json-body]   (prints status line, Location, body)
# Local Kill Bill only. Admin login is the image's default (admin/password); API keys are test values.
KEY=$1; M=$2; P=$3; D=${4:-}
if [ -n "$D" ]; then
  curl -si -u admin:password -X "$M" "http://127.0.0.1:8480/1.0/kb$P" -H 'Content-Type: application/json' \
    -H 'Accept: application/json' -H 'X-Killbill-CreatedBy: lt' -H "X-Killbill-ApiKey: $KEY" -H "X-Killbill-ApiSecret: secret${KEY#tenant}" -d "$D" \
    | grep -Ei '^(HTTP|Location|\{|\[)'
else
  curl -si -u admin:password -X "$M" "http://127.0.0.1:8480/1.0/kb$P" -H 'Accept: application/json' \
    -H 'X-Killbill-CreatedBy: lt' -H "X-Killbill-ApiKey: $KEY" -H "X-Killbill-ApiSecret: secret${KEY#tenant}" \
    | grep -Ei '^(HTTP|Location|\{|\[)'
fi
