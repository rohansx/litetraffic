#!/bin/sh
# External (built-in) payment plugin: single over-refund, then capture+refund variants. Output: raw/07-*.txt
cd "$(dirname "$0")"
ID=$(sh kb.sh tenantA POST /accounts '{"name":"probe4","email":"p4@example.com","currency":"USD"}' | grep -i '^Location' | sed 's|.*/||' | tr -d '\r')
sh kb.sh tenantA POST "/accounts/$ID/paymentMethods?isDefault=true" '{"pluginName":"__EXTERNAL_PAYMENT__"}' >/dev/null
echo "--- purchase 100, then ONE refund of 150"
sh kb.sh tenantA POST "/accounts/$ID/payments" '{"transactionType":"PURCHASE","amount":100,"currency":"USD"}'
PID=$(sh kb.sh tenantA GET "/accounts/$ID/payments" | grep -o '"paymentId":"[^"]*"' | head -1 | cut -d'"' -f4)
sh kb.sh tenantA POST "/payments/$PID/refunds" '{"amount":150}'
sh kb.sh tenantA GET "/payments/$PID" | grep -o '"refundedAmount":[0-9.]*\|"purchasedAmount":[0-9.]*'
echo "--- auth 100, capture 100, refund 60, refund 60"
PK=pk-$(date +%s)
sh kb.sh tenantA POST "/accounts/$ID/payments" "{\"transactionType\":\"AUTHORIZE\",\"amount\":100,\"currency\":\"USD\",\"paymentExternalKey\":\"$PK\",\"transactionExternalKey\":\"$PK-a\"}"
sh kb.sh tenantA POST "/payments" "{}" > /dev/null
P2=$(sh kb.sh tenantA GET "/accounts/$ID/payments" | grep -o '"paymentId":"[^"]*"' | tail -1 | cut -d'"' -f4)
sh kb.sh tenantA POST "/payments/$P2" "{\"amount\":100,\"transactionExternalKey\":\"$PK-c\"}"
sh kb.sh tenantA POST "/payments/$P2/refunds" "{\"amount\":60,\"transactionExternalKey\":\"$PK-r1\"}"
sh kb.sh tenantA POST "/payments/$P2/refunds" "{\"amount\":60,\"transactionExternalKey\":\"$PK-r2\"}"
sh kb.sh tenantA GET "/payments/$P2" | grep -o '"refundedAmount":[0-9.]*\|"capturedAmount":[0-9.]*'
