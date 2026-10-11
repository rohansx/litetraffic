# Gitea: a real application as the target

[Gitea](https://github.com/go-gitea/gitea) is the outside-app check for the preview: an unfamiliar, widely used HTTP/JSON application with an OpenAPI description, token authentication and real multi-user authorization. Two scenarios run against a stock container; nothing in Gitea is modified.

| Scenario | What it checks | Fixture |
|---|---|---|
| `kit.json` (via `init tenant-isolation`) | Alice and Bob each own a private repository: own reads succeed, cross-tenant reads and the `PATCH` description write are refused, the victim repository is unchanged, anonymous reads are refused, and no response carries the other user's marker | None (static repositories created once below) |
| `manifest.json` (`gitea-issues`) | Every journey files an issue and reads it back; the final observation polls the repository until `open_issues_count` equals the planned journeys | `setup.sh` recreates `alice/lt-issues`; `teardown.sh` deletes it |

## Run it

```bash
docker run -d --name lt-gitea -p 3030:3000 -e GITEA__security__INSTALL_LOCK=true \
  -e GITEA__server__ROOT_URL=http://127.0.0.1:3030/ -e GITEA__service__DISABLE_REGISTRATION=true gitea/gitea:latest
sleep 10
g() { docker exec -u git lt-gitea gitea "$@"; }
for u in alice bob; do g admin user create --username $u --password "Local-$u-pass1" --email $u@example.test --must-change-password=false; done
export GITEA_TOKEN_ALICE=$(g admin user generate-access-token --username alice --token-name lt --scopes all --raw)
export GITEA_TOKEN_BOB=$(g admin user generate-access-token --username bob --token-name lt --scopes all --raw)
A=http://127.0.0.1:3030/api/v1
curl -s -o /dev/null -X POST -H "Authorization: token $GITEA_TOKEN_ALICE" -H 'Content-Type: application/json' $A/user/repos \
  -d '{"name":"alice-private","private":true,"auto_init":true,"description":"alice-secret-marker-7f3a"}'
curl -s -o /dev/null -X POST -H "Authorization: token $GITEA_TOKEN_BOB" -H 'Content-Type: application/json' $A/user/repos \
  -d '{"name":"bob-private","private":true,"auto_init":true,"description":"bob-secret-marker-c91e"}'

litetraffic init tenant-isolation --config examples/gitea/kit.json --out gitea-isolation
litetraffic verify gitea-isolation --target http://127.0.0.1:3030 --capture docker:lt-gitea   # pass
litetraffic verify examples/gitea --target http://127.0.0.1:3030                               # pass
```

The markers are the repositories' descriptions: private text that must never appear in the other user's responses.

## See it catch a real authorization change

Make Bob a read-only collaborator on Alice's repository and run the kit again:

```bash
curl -s -o /dev/null -X PUT -H "Authorization: token $GITEA_TOKEN_ALICE" -H 'Content-Type: application/json' \
  $A/repos/alice/alice-private/collaborators/bob -d '{"permission":"read"}'
litetraffic verify gitea-isolation --target http://127.0.0.1:3030   # FAIL: cross_tenant_read_blocked, writes still blocked
curl -s -o /dev/null -X DELETE -H "Authorization: token $GITEA_TOKEN_ALICE" $A/repos/alice/alice-private/collaborators/bob
```

## What the first run taught

- Gitea's token authentication writes to the database on every request (last-used stamps). On SQLite that serialises requests, so latency grows with concurrency: 25 ms alone, 400 ms with six in flight, over a second when cold. The kit's journey is 18 requests, so the first run against a cold container timed out — which is why `kit.json` sets `drain_seconds`.
- A client that disconnects mid-request shows up in Gitea's log as `500`/`401 ... context canceled`. Those lines in a captured `server/lt-gitea.log` mean k6 was stopped, not that Gitea failed.
