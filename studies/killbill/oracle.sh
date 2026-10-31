#!/bin/sh
# Does tenant B see a difference between "account exists in tenant A" and "no such account"? Output: raw/12-*.txt
cd "$(dirname "$0")"
H1="X-Killbill-ApiKey: lt-tenant-b"
H2="X-Killbill-ApiSecret: lt-local-secret-b"
A_ID=$(curl -s -u admin:password -H "X-Killbill-ApiKey: lt-tenant-a" -H "X-Killbill-ApiSecret: lt-local-secret-a" -H 'Accept: application/json' "http://127.0.0.1:8480/1.0/kb/accounts/pagination?limit=1" | grep -o '"accountId":"[^"]*"' | head -1 | cut -d'"' -f4)
echo "account in tenant A: $A_ID"
echo "--- tenant B asks for it:"
curl -s -u admin:password -H "$H1" -H "$H2" -H 'Accept: application/json' -w '\nHTTP %{http_code}\n' "http://127.0.0.1:8480/1.0/kb/accounts/$A_ID"
echo "--- tenant B asks for an id that exists nowhere:"
curl -s -u admin:password -H "$H1" -H "$H2" -H 'Accept: application/json' -w '\nHTTP %{http_code}\n' "http://127.0.0.1:8480/1.0/kb/accounts/11111111-2222-3333-4444-555555555555"
