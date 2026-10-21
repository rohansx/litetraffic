#!/bin/sh
# Auth, capture, then two refunds of 60 on a 100 capture, with the payment-test plugin. Output: raw/06-*.txt
cd "$(dirname "$0")"
ID=$(sh kb.sh tenantA POST /accounts '{"name":"probe3","email":"p3@example.com","currency":"USD"}' | grep -i '^Location' | sed 's|.*/||' | tr -d '\r')
sh kb.sh tenantA POST "/accounts/$ID/paymentMethods?isDefault=true" '{"pluginName":"killbill-payment-test"}' >/dev/null
echo "--- auth, capture, refund 60, refund 60"
PK=pk-$(date +%s)
sh kb.sh tenantA POST "/accounts/$ID/payments" "{\"transactionType\":\"AUTHORIZE\",\"amount\":100,\"currency\":\"USD\",\"paymentExternalKey\":\"$PK\",\"transactionExternalKey\":\"$PK-a\"}"
PID=$(sh kb.sh tenantA GET "/accounts/$ID/payments" | grep -o '"paymentId":"[^"]*"' | head -1 | cut -d'"' -f4)
sh kb.sh tenantA POST "/payments/$PID" "{\"amount\":100,\"transactionExternalKey\":\"$PK-c\"}"
sh kb.sh tenantA POST "/payments/$PID/refunds" "{\"amount\":60,\"transactionExternalKey\":\"$PK-r1\"}"
sh kb.sh tenantA POST "/payments/$PID/refunds" "{\"amount\":60,\"transactionExternalKey\":\"$PK-r2\"}"
sh kb.sh tenantA GET "/payments/$PID" | grep -o '"refundedAmount":[0-9.]*\|"capturedAmount":[0-9.]*\|"transactionType":"[A-Z]*","amount":[0-9.]*\|"status":"[A-Z_]*"'
