# Run LiteTraffic in CI

LiteTraffic is a CLI, so any CI that can start your app and run Python can run it. This page gives a GitHub Actions recipe; the same steps work in GitLab CI or anything else.

The job starts your app, runs one or more scenarios against it, fails the build on a failing verdict, and keeps the run folder as a build artifact so anyone can open it later with `litetraffic dashboard`.

## Exit codes

`litetraffic verify` exits with the verdict, so CI needs no parsing to pass or fail:

| Exit | Meaning | What CI should do |
|---|---|---|
| `0` | `pass`: every check held with complete evidence | pass |
| `1` | `fail`: at least one business check failed | fail the build |
| `2` | `inconclusive`: evidence missing, so the run proves nothing | fail the build (an unproven run is not a pass) |
| `3` | `error`, or a usage or input problem (bad target, invalid scenario, k6 missing) | fail the build and fix the setup |
| `130` | cancelled (Ctrl+C or the job was stopped) | treat as cancelled |

## GitHub Actions

Save as `.github/workflows/litetraffic.yml` and replace the "Start the app" step with however your app starts (a compose file, a dev server, a preview URL).

```yaml
name: LiteTraffic

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  verify:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install k6 v2.2.0 (checksum-verified)
        run: |
          curl --fail --location --output /tmp/k6.tar.gz \
            https://github.com/grafana/k6/releases/download/v2.2.0/k6-v2.2.0-linux-amd64.tar.gz
          printf '%s  %s\n' \
            b5a8003c86f35f5cd5ceef1490312c48e587696c94d998cefc6d7b3b4cb1597d \
            /tmp/k6.tar.gz | sha256sum --check -
          tar -xzf /tmp/k6.tar.gz -C /tmp
          sudo install -m 755 /tmp/k6-v2.2.0-linux-amd64/k6 /usr/local/bin/k6

      - name: Install LiteTraffic
        # Until the PyPI release, install from the repository. Pin a commit for reproducible CI.
        run: python -m pip install "litetraffic @ git+https://github.com/rohansx/litetraffic@main"

      - name: Start the app
        run: |
          docker compose up -d --wait        # or: npm start &  /  your own start script
          litetraffic doctor --target http://127.0.0.1:3000/health

      - name: Verify
        id: verify
        run: |
          set +e
          litetraffic verify scenarios/checkout \
            --target http://127.0.0.1:3000 --seed "${{ github.run_number }}" \
            --capture docker:api,worker \
            --json > verify.json
          echo "exit=$?" >> "$GITHUB_OUTPUT"

      - name: Job summary
        if: always()
        run: |
          python - <<'EOF' >> "$GITHUB_STEP_SUMMARY"
          import json
          r = json.load(open("verify.json"))
          print(f"### LiteTraffic: {r.get('verdict', 'error').upper()}")
          print(f"Run `{r.get('run_id')}` · lifecycle `{r.get('lifecycle')}` · seed `{r.get('seed')}`\n")
          print("| Check | Status | Samples |\n|---|---|---|")
          for a in r.get("assertions", []):
              print(f"| {a['id']} | {a['status']} | {a.get('samples', '')} |")
          for name, j in (r.get("journeys") or {}).items():
              reached = ", ".join(f"{s['name']} {s['reached']}" for s in j["stages"])
              print(f"\n**{name}**: {j['started']} started; {reached}")
          EOF

      - name: Keep the run evidence
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: litetraffic-runs
          path: .litetraffic/runs/
          retention-days: 14

      - name: Fail on a non-passing verdict
        if: steps.verify.outputs.exit != '0'
        run: exit ${{ steps.verify.outputs.exit }}
```

Notes:

- **Seeds.** Using the run number as the seed explores a different schedule each build while keeping each build reproducible: rerun locally with the same `--seed` to get the same schedule. Use a fixed seed instead when you want builds to be directly comparable with `litetraffic diff`.
- **`--capture`** is optional. It records the named containers' logs and CPU/memory into the run (`server.json`); drop it if the app does not run in Docker. Capture problems never change the verdict.
- **Secrets.** Pass tokens through `env:` from GitHub secrets. Scenarios name the variables they read; values never appear in manifests or run artifacts.
- **Opening a run.** Download the `litetraffic-runs` artifact, unzip it into `.litetraffic/runs/`, then `litetraffic dashboard`. The run folder is also safe to attach to an issue: it holds no secret values.
- **Several scenarios.** Repeat the Verify step per scenario with its own `--json` file, or loop over `scenarios/*/` and fail if any exit code is non-zero.
