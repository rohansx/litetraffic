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

## File an issue draft when a run fails

When the verdict is `fail`, `litetraffic report issue` can turn the run into a scrubbed issue draft for the app's maintainers: a title, the failing checks, a `repro.sh` that does not need LiteTraffic, and (with the `video` extra) a replay video. In CI, draft on failure and keep the draft as a build artifact. Filing stays a separate, explicit opt-in.

Add these steps after **Verify** and before **Fail on a non-passing verdict**, and install the extra so the draft gets its video:

```yaml
      - name: Install LiteTraffic
        run: python -m pip install "litetraffic[video] @ git+https://github.com/rohansx/litetraffic@main"

      # ... Start the app, Verify ...

      - name: Draft an issue for a failing run
        if: steps.verify.outputs.exit == '1'
        continue-on-error: true        # a drafting problem must not hide the verdict
        run: |
          run_id=$(python -c 'import json; print(json.load(open("verify.json"))["run_id"])')
          litetraffic report issue "$run_id" --scenario scenarios/checkout \
            --repo your-org/your-app --json > issue.json
          # To file it as well, add --submit here and give the step a token (see below).
          # Read a few drafts before you turn that on.

      - name: Keep the issue draft
        if: steps.verify.outputs.exit == '1'
        uses: actions/upload-artifact@v4
        with:
          name: litetraffic-issue
          path: .litetraffic/runs/*/issue/
          retention-days: 30
```

Notes:

- **Draft only by default.** Without `--submit` the command writes `RUN/issue/` and prints the `gh` command it would run; nothing is sent. Download the `litetraffic-issue` artifact, read `issue.md`, and file it yourself, or run `litetraffic report issue RUN --repo OWNER/NAME --submit` locally against the downloaded run.
- **Only `fail` runs.** `inconclusive` and `error` runs exit `3` from `report issue` (missing evidence is not a bug report) and a `pass` run writes nothing, which is why the step is gated on exit `1`.
- **Evidence link.** Inside GitHub Actions the draft links to the workflow run (`GITHUB_SERVER_URL`, `GITHUB_REPOSITORY`, `GITHUB_RUN_ID`) unless `--attach-url` is given. That link needs a GitHub sign-in and stops working when the artifact retention ends; the draft says so.
- **Turning on filing.** Filing with `--submit` uses the `gh` CLI with `GH_TOKEN` set from a secret. The workflow's `GITHUB_TOKEN` can only file in its own repository (with `permissions: issues: write`), and as an installation token it cannot upload the video, so the issue gets a "drag the video in" note instead. To file in another repository, use a fine-grained token with issue access to that repository. Duplicates are found by fingerprint, so a failure that keeps happening is filed once; with `--comment`, later runs are added to the open issue as comments instead of being refused.
- **Security failures never go public.** A security-class failure (for example a tenant-isolation check) is drafted as `security.md` and, with `--submit`, sent only as a private vulnerability report, without attachments. Workflow artifacts of a public repository can be downloaded by anyone signed in to GitHub, so for security scenarios in a public repository drop the **Keep the issue draft** step (and keep the run artifact private or short-lived).
- **`--remove-local`** is rarely useful in CI, since the runner is discarded anyway; keep the run artifact so the issue's evidence link works.
