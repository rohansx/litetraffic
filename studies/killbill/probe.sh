#!/bin/sh
# One purchase and one refund by hand, to learn the API. Output: raw/03-probe-payment.txt
cd "$(dirname "$0")"
ID=$(sh kb.sh tenantA POST /accounts '{"name":"probe2","email":"p2@example.com","currency":"USD"}' | grep -i '^Location' | sed 's|.*/||' | tr -d '\r')
echo "account $ID"
sh kb.sh tenantA POST "/accounts/$ID/paymentMethods?isDefault=true" '{"pluginName":"killbill-payment-test"}'
sh kb.sh tenantA POST "/accounts/$ID/payments" '{"transactionType":"PURCHASE","amount":100,"currency":"USD"}'
PID=$(sh kb.sh tenantA GET "/accounts/$ID/payments" | grep -o '"paymentId":"[^"]*"' | head -1 | cut -d'"' -f4)
echo "payment $PID"
sh kb.sh tenantA GET "/payments/$PID"
echo "--- refund 60"
sh kb.sh tenantA POST "/payments/$PID/refunds" '{"amount":60}'
echo "--- refund 60 again (should be refused: 120 > 100)"
sh kb.sh tenantA POST "/payments/$PID/refunds" '{"amount":60}'
sh kb.sh tenantA GET "/payments/$PID"
echo "--- tenant B reads tenant A account"
sh kb.sh tenantB GET "/accounts/$ID"
