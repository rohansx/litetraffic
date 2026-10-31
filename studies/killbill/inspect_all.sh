#!/bin/sh
# Validate every scenario bundle with `litetraffic inspect`. Set LITETRAFFIC to the CLI path if it is not on PATH.
cd "$(dirname "$0")"
LT=${LITETRAFFIC:-litetraffic}
for s in scenarios/*; do
  echo "== $s"
  $LT inspect "$s" 2>&1 | tail -4
done
