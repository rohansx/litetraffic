# Cause: N buyers reserve with ONE single-use 100%-off promo code, then complete their orders.
# `litetraffic report issue --scenario DIR` includes this file (by convention, DIR/repro.sh) in the
# generated standalone repro. It runs under `set -euo pipefail` with TARGET and
# LT_HIEVENTS_ORG_TOKEN already checked, and must leave the promo it creates at /data/0 of
# /api/events/$EVENT_ID/promo-codes, which the generated effect check reads afterwards.
N="${N:-10}"
EVENT_ID="${LT_HIEVENTS_EVENT_ID:-1}"
API="$TARGET/api"
AUTH="Authorization: Bearer ${LT_HIEVENTS_ORG_TOKEN}"
JSON='Content-Type: application/json'
STAMP="$(date +%s)"
CODE="ltrepro${STAMP}"

# The event's default category carries the product and its price (as setup.sh does).
CATS=$(curl -sf -H "$AUTH" -H 'Accept: application/json' "$API/events/$EVENT_ID/product-categories")
PRODUCT_ID=$(printf '%s' "$CATS" | python3 -c "import sys,json;print(json.load(sys.stdin)['data'][0]['products'][0]['id'])")
PRICE_ID=$(printf '%s' "$CATS" | python3 -c "import sys,json;print(json.load(sys.stdin)['data'][0]['products'][0]['prices'][0]['id'])")

# Publish the event, then drop older promo codes so this run's code is the only one (/data/0).
curl -sf -o /dev/null -X PUT -H "$AUTH" -H "$JSON" "$API/events/$EVENT_ID/status" -d '{"status":"LIVE"}' || true
for pid in $(curl -sf -H "$AUTH" -H 'Accept: application/json' "$API/events/$EVENT_ID/promo-codes" \
  | python3 -c "import sys,json;[print(p['id']) for p in json.load(sys.stdin)['data']]" 2>/dev/null || true); do
  curl -sf -o /dev/null -X DELETE -H "$AUTH" "$API/events/$EVENT_ID/promo-codes/$pid" || true
done

PROMO=$(curl -sf -X POST -H "$AUTH" -H "$JSON" "$API/events/$EVENT_ID/promo-codes" \
  -d "{\"code\":\"$CODE\",\"discount_type\":\"PERCENTAGE\",\"discount\":100,\"max_allowed_usages\":1,\"applicable_product_ids\":[]}")
PROMO_ID=$(printf '%s' "$PROMO" | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['id'])")
echo "created promo $CODE (id $PROMO_ID) with max_allowed_usages=1"

WORK=$(mktemp -d)
ORDER_URL="$API/public/events/$EVENT_ID/order"
# 1) Every buyer reserves with the promo, in parallel, each with its own checkout session.
for i in $(seq 1 "$N"); do
  (
    session="ltrepro${i}x${STAMP}"
    curl -s -X POST -H "$JSON" -H 'Accept: application/json' "$ORDER_URL?session_identifier=$session" \
      -d "{\"products\":[{\"product_id\":$PRODUCT_ID,\"quantities\":[{\"price_id\":$PRICE_ID,\"quantity\":1}]}],\"promo_code\":\"$CODE\"}" \
      | python3 -c "import sys,json;d=json.load(sys.stdin).get('data') or {};print(d.get('short_id') or '', d.get('total_gross'))" \
      > "$WORK/$i" || true
  ) &
done
wait
# 2) Then every buyer completes; a free (discounted) order needs no payment and becomes COMPLETED.
for i in $(seq 1 "$N"); do
  (
    session="ltrepro${i}x${STAMP}"
    read -r short total < "$WORK/$i" || exit 0
    [ -n "$short" ] || exit 0
    email="${session}@example.test"
    curl -s -o /dev/null -X PUT -H "$JSON" -H 'Accept: application/json' "$ORDER_URL/$short?session_identifier=$session" \
      -d "{\"order\":{\"first_name\":\"Repro\",\"last_name\":\"b$i\",\"email\":\"$email\",\"email_confirmation\":\"$email\"},\"products\":[{\"product_id\":$PRODUCT_ID,\"product_price_id\":$PRICE_ID,\"first_name\":\"Repro\",\"last_name\":\"b$i\",\"email\":\"$email\",\"email_confirmation\":\"$email\"}]}" || true
  ) &
done
wait
echo "free reservations: $(cat "$WORK"/* | awk '$2 == "0"' | wc -l | tr -d ' ') of $N (a single-use code should allow 1)"
rm -rf "$WORK"
