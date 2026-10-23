#!/bin/sh
# One-time: create event `lab` (draft, test mode) with one 20.00 EUR ticket and bank transfer enabled.
# Needs PRETIX_TOKEN. Idempotent enough to re-run on a fresh database.
set -eu
O=${LT_TARGET:-http://localhost:8345}/api/v1/organizers/study
H="Authorization: Token ${PRETIX_TOKEN:?set PRETIX_TOKEN}"
j() { curl -s -w '\nHTTP %{http_code}\n' -H "$H" -H 'Content-Type: application/json' "$@"; }
j -X PATCH $O/ -d '{"plugins":["pretix.plugins.banktransfer"]}'
j -X POST $O/events/ -d '{"name":{"en":"Lab"},"slug":"lab","live":false,"currency":"EUR","date_from":"2027-03-01T18:00:00Z","has_subevents":false,"testmode":true}'
j -X PATCH $O/events/lab/ -d '{"plugins":["pretix.plugins.banktransfer"]}'
j -X PATCH $O/events/lab/settings/ -d '{"payment_banktransfer__enabled":true,"payment_banktransfer_bank_details_type":"other","payment_banktransfer_bank_details":{"en":"x"}}'
j -X POST $O/events/lab/items/ -d '{"name":{"en":"Ticket"},"default_price":"20.00","admission":true,"active":true}'
