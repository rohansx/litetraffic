#!/bin/sh
# Invoice by external charge, pay it, try to pay again, adjust. Output: raw/08-*.txt
cd "$(dirname "$0")"
K=tenantA
ID=$(sh kb.sh $K POST /accounts '{"name":"probe5","email":"p5@example.com","currency":"USD"}' | grep -i '^Location' | sed 's|.*/||' | tr -d '\r')
echo "account $ID"
PM=$(sh kb.sh $K POST "/accounts/$ID/paymentMethods?isDefault=false" '{"pluginName":"__EXTERNAL_PAYMENT__"}' | grep -i '^Location' | sed 's|.*/||' | tr -d '\r')
echo "pm $PM"
echo "--- external charge 100"
sh kb.sh $K POST "/invoices/charges/$ID?autoCommit=true" "[{\"accountId\":\"$ID\",\"amount\":100,\"currency\":\"USD\",\"description\":\"lt charge\"}]"
INV=$(sh kb.sh $K GET "/accounts/$ID/invoices" | grep -o '"invoiceId":"[^"]*"' | head -1 | cut -d'"' -f4)
echo "invoice $INV"
sh kb.sh $K GET "/invoices/$INV" | cut -c1-700
echo "--- pay 100"
sh kb.sh $K POST "/invoices/$INV/payments?externalPayment=false" "{\"accountId\":\"$ID\",\"purchasedAmount\":100,\"paymentMethodId\":\"$PM\"}"
echo "--- pay 100 again"
sh kb.sh $K POST "/invoices/$INV/payments?externalPayment=false" "{\"accountId\":\"$ID\",\"purchasedAmount\":100,\"paymentMethodId\":\"$PM\"}"
sh kb.sh $K GET "/accounts?externalKey=none" | head -2
sh kb.sh $K GET "/accounts/$ID?accountWithBalance=true" | cut -c1-600
echo "--- adjust"
ITEM=$(sh kb.sh $K GET "/invoices/$INV" | grep -o '"invoiceItemId":"[^"]*"' | head -1 | cut -d'"' -f4)
echo "item $ITEM"
sh kb.sh $K POST "/invoices/$INV" "{\"accountId\":\"$ID\",\"invoiceItemId\":\"$ITEM\",\"amount\":100}"
sh kb.sh $K POST "/invoices/$INV" "{\"accountId\":\"$ID\",\"invoiceItemId\":\"$ITEM\",\"amount\":100}"
sh kb.sh $K GET "/accounts/$ID?accountWithBalance=true" | grep -o '"accountBalance":[-0-9.]*'
sh kb.sh $K GET "/invoices/$INV" | grep -o '"amount":[-0-9.]*,"currency":"USD","status[^,]*\|"balance":[-0-9.]*\|"creditAdj":[-0-9.]*\|"refundAdj":[-0-9.]*'
