from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Sequence

from pydantic import ValidationError

from litetraffic.activity import up
from litetraffic.approval import approve, require_approval
from litetraffic.compare import ComparisonError, compare_runs
from litetraffic.dashboard import serve
from litetraffic.doctor import run_doctor
from litetraffic.e2b import resolve_target
from litetraffic.models import resolve_expected
from litetraffic.human import format_diff, format_inspect, format_verify
from litetraffic.runner import RunnerError, repeat_verify, verify
from litetraffic.runs import prune, resolve
from litetraffic.scenario import ScenarioError, load_scenario


def _add_scenario(command: argparse.ArgumentParser) -> None:
    command.add_argument("scenario", type=Path, nargs="?")
    command.add_argument("--scenario", dest="scenario_flag", type=Path, metavar="SCENARIO")


def _port(value: str) -> int:
    port = int(value)
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError(f"port must be 0-65535, got {port}")
    return port


def _scenario(args: argparse.Namespace) -> Path:
    if (args.scenario is None) == (args.scenario_flag is None):
        raise ValueError("give the scenario directory once, either positionally or with --scenario")
    return args.scenario or args.scenario_flag


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="litetraffic")
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser("doctor", help="check local prerequisites and target reachability")
    doctor.add_argument("--target")
    doctor.add_argument("--k6-path")
    doctor.add_argument("--output-dir", type=Path, default=Path(".litetraffic/runs"))
    doctor.add_argument("--json", action="store_true")

    inspect = commands.add_parser("inspect", help="validate and explain a scenario bundle")
    _add_scenario(inspect)
    inspect.add_argument("--seed", type=int, default=0)
    inspect.add_argument("--json", action="store_true")

    verify_command = commands.add_parser("verify", help="run a finite scenario and evaluate its evidence")
    _add_scenario(verify_command)
    verify_command.add_argument("--target")
    verify_command.add_argument("--e2b-sandbox-id")
    verify_command.add_argument("--e2b-port", type=int)
    verify_command.add_argument("--output-dir", type=Path, default=Path(".litetraffic/runs"))
    verify_command.add_argument("--k6-path")
    verify_command.add_argument("--seed", type=int, default=0)
    verify_command.add_argument("--repeat", type=int, default=1)
    verify_command.add_argument("--same-seed", action="store_true", help="repeat --seed instead of consecutive seeds")
    verify_command.add_argument("--require-approval", action="store_true", help="refuse to run an unapproved digest/origin")
    verify_command.add_argument("--approved-digest", metavar="SHA", help="CI approval: must equal the scenario digest")
    verify_command.add_argument("--json", action="store_true")

    up_command = commands.add_parser("up", help="run repeated bounded slices as a background activity (no verdict)")
    _add_scenario(up_command)
    up_command.add_argument("--target", required=True)
    up_command.add_argument("--output-dir", type=Path, default=Path(".litetraffic/runs"))
    up_command.add_argument("--k6-path")
    up_command.add_argument("--seed", type=int, default=0, help="seed of the first slice; each slice adds one")
    up_command.add_argument("--max-slices", type=int, metavar="N", help="stop after N slices (default: until Ctrl-C)")
    up_command.add_argument("--json", action="store_true")

    approve_command = commands.add_parser("approve", help="bind a scenario digest to a target profile and origin")
    _add_scenario(approve_command)
    approve_command.add_argument("--target-profile", required=True, metavar="NAME")
    approve_command.add_argument("--target", required=True, metavar="URL")
    approve_command.add_argument("--json", action="store_true")

    diff_command = commands.add_parser("diff", help="compare compatible run artifacts")
    diff_command.add_argument("baseline", help="run directory or run ID")
    diff_command.add_argument("candidate", help="run directory or run ID")
    diff_command.add_argument("--runs-dir", type=Path, default=Path(".litetraffic/runs"))
    diff_command.add_argument("--max-p95-regression-percent", type=float)
    diff_command.add_argument("--json", action="store_true")

    dashboard = commands.add_parser("dashboard", help="browse run artifacts on a local web page")
    dashboard.add_argument("--runs-dir", type=Path, default=Path(".litetraffic/runs"))
    dashboard.add_argument("--port", type=_port, default=8780)

    prune_command = commands.add_parser("prune", help="delete old run directories")
    prune_command.add_argument("--runs-dir", type=Path, default=Path(".litetraffic/runs"))
    prune_command.add_argument("--keep", type=int, help="keep the newest N runs")
    prune_command.add_argument("--older-than", type=float, metavar="DAYS", help="delete runs finished more than DAYS ago")
    prune_command.add_argument("--dry-run", action="store_true", help="list what would be deleted")
    prune_command.add_argument("--json", action="store_true")
    return parser


def _emit(value: dict, as_json: bool, formatter: Callable[[dict], list[str]] | None = None) -> None:
    if as_json:
        print(json.dumps(value, sort_keys=True))
        return
    if formatter is not None:
        print("\n".join(formatter(value)))
        return
    for key, item in value.items():
        if key == "checks":
            for check in item:
                marker = "ok" if check["ok"] else "failed"
                print(f"{check['name']}: {marker} - {check['detail']}")
        else:
            print(f"{key}: {item}")


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "doctor":
            report = run_doctor(target=args.target, k6_path=args.k6_path, output_dir=args.output_dir)
            payload = {"ok": report.ok, "checks": [check.model_dump() for check in report.checks]}
            _emit(payload, args.json)
            return 0 if report.ok else 3

        if args.command == "dashboard":
            return serve(args.runs_dir, args.port)

        if args.command == "prune":
            paths = prune(args.runs_dir, args.keep, args.older_than, args.dry_run)
            payload = {"ok": True, "dry_run": args.dry_run, "pruned": [str(path) for path in paths]}
            verb = "would delete" if args.dry_run else "deleted"
            _emit(payload, args.json, lambda result: [f"{verb}: {path}" for path in result["pruned"]] or ["nothing to prune"])
            return 0

        if args.command in {"inspect", "verify", "approve", "up"}:
            args.scenario = _scenario(args)

        if args.command == "approve":
            record = approve(load_scenario(args.scenario).digest, args.target_profile, args.target)
            _emit({"ok": True, **record}, args.json)
            return 0

        if args.command == "up":
            def log(message: str) -> None:
                print(message, file=sys.stderr, flush=True)

            activity = up(args.target, args.scenario, args.output_dir, args.k6_path, args.seed, args.max_slices, log)
            log(f"status: {activity['status']}")
            if args.json:
                _emit(activity, True)
            # Ctrl-C is the normal way to stop an activity, so a stopped activity exits 0; errors raise (exit 3).
            return 0

        if args.command == "verify":
            if args.repeat < 1:
                raise ValueError("repeat must be at least 1")
            if args.same_seed and args.repeat < 2:
                raise ValueError("--same-seed requires --repeat of at least 2")
            target = resolve_target(args.target, args.e2b_sandbox_id, args.e2b_port)
            if args.require_approval or args.approved_digest is not None:
                require_approval(load_scenario(args.scenario).digest, target, args.approved_digest)
            if args.repeat == 1:
                payload = verify(target, args.scenario, args.output_dir, args.k6_path, args.seed)
            else:
                payload = repeat_verify(
                    target,
                    args.scenario,
                    args.output_dir,
                    args.k6_path,
                    args.seed,
                    args.repeat,
                    same_seed=args.same_seed,
                )
            _emit(payload, args.json, lambda result: format_verify(result, args.output_dir))
            if payload["lifecycle"] == "cancelled":
                return 130
            return {"pass": 0, "fail": 1, "inconclusive": 2}.get(payload["verdict"], 3)

        if args.command == "diff":
            baseline, candidate = (resolve(ref, args.runs_dir) for ref in (args.baseline, args.candidate))
            payload = compare_runs(baseline, candidate, args.max_p95_regression_percent)
            _emit(payload, args.json, format_diff)
            return {"pass": 0, "fail": 1}.get(payload["verdict"], 2)

        bundle = load_scenario(args.scenario)
        manifest = bundle.manifest
        resolved_schedule = manifest.schedule.resolve(args.seed)
        owned, observation = manifest.fixtures.owned_http, manifest.observation
        payload = {
            "ok": True,
            "name": manifest.name,
            "schema_version": manifest.schema_version,
            "script": str(bundle.script_path),
            "scenario_sha256": bundle.digest,
            "planned_journeys": manifest.planned_journeys,
            "maximum_journey_requests": manifest.maximum_journey_requests,
            "maximum_observation_requests": int(manifest.observation is not None),
            "maximum_journey_writes": manifest.maximum_journey_writes,
            "resolved_schedule": [phase.model_dump(exclude={"admitted_journeys"}) for phase in resolved_schedule],
            "assertions": manifest.assertions,
            "actors": [actor.model_dump(by_alias=True, exclude_none=True) for actor in manifest.actors],
            "budgets": manifest.budgets.model_dump(),
            "fixture": {
                "recipe": manifest.fixtures.recipe,
                "owned_http": owned and owned.model_dump(include={"create_path", "delete_path"}),
                "command": manifest.fixtures.command and manifest.fixtures.command.model_dump(),
            },
            # Names only: the environment is never read here.
            "secret_env": sorted(
                {ref for ref in (owned and owned.bearer_token_env, observation and observation.bearer_token_env) if ref}
                | set(observation.headers_env.values() if observation else ())
                | {actor.auth.secret_env for actor in manifest.actors if actor.auth}
            ),
            "observer": manifest.observer,
            "observation_path": observation and observation.path,
            "observation_expected": observation
            and resolve_expected(observation.expected, {"planned_journeys": manifest.planned_journeys, "seed": args.seed}),
        }
        _emit(payload, args.json, format_inspect)
        return 0
    except (ComparisonError, RunnerError, ScenarioError, ValidationError, ValueError, OSError) as exc:
        _emit({"ok": False, "error": str(exc)}, getattr(args, "json", False))
        return 3


def entrypoint() -> None:
    raise SystemExit(main())
