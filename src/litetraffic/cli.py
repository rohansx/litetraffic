from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from pydantic import ValidationError

from litetraffic.compare import ComparisonError, compare_runs
from litetraffic.doctor import run_doctor
from litetraffic.runner import RunnerError, repeat_verify, verify
from litetraffic.scenario import ScenarioError, load_scenario


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="litetraffic")
    commands = parser.add_subparsers(dest="command", required=True)

    doctor = commands.add_parser("doctor", help="check local prerequisites and target reachability")
    doctor.add_argument("--target")
    doctor.add_argument("--k6-path")
    doctor.add_argument("--json", action="store_true")

    inspect = commands.add_parser("inspect", help="validate and explain a scenario bundle")
    inspect.add_argument("scenario", type=Path)
    inspect.add_argument("--seed", type=int, default=0)
    inspect.add_argument("--json", action="store_true")

    verify_command = commands.add_parser("verify", help="run a finite scenario and evaluate its evidence")
    verify_command.add_argument("scenario", type=Path)
    verify_command.add_argument("--target", required=True)
    verify_command.add_argument("--output-dir", type=Path, default=Path(".litetraffic/runs"))
    verify_command.add_argument("--k6-path")
    verify_command.add_argument("--seed", type=int, default=0)
    verify_command.add_argument("--repeat", type=int, default=1)
    verify_command.add_argument("--json", action="store_true")

    diff_command = commands.add_parser("diff", help="compare compatible run artifacts")
    diff_command.add_argument("baseline", type=Path)
    diff_command.add_argument("candidate", type=Path)
    diff_command.add_argument("--max-p95-regression-percent", type=float)
    diff_command.add_argument("--json", action="store_true")
    return parser


def _emit(value: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(value, sort_keys=True))
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
            report = run_doctor(target=args.target, k6_path=args.k6_path)
            payload = {"ok": report.ok, "checks": [check.model_dump() for check in report.checks]}
            _emit(payload, args.json)
            return 0 if report.ok else 3

        if args.command == "verify":
            if args.repeat < 1:
                raise ValueError("repeat must be at least 1")
            if args.repeat == 1:
                payload = verify(args.target, args.scenario, args.output_dir, args.k6_path, args.seed)
            else:
                payload = repeat_verify(
                    args.target,
                    args.scenario,
                    args.output_dir,
                    args.k6_path,
                    args.seed,
                    args.repeat,
                )
            _emit(payload, args.json)
            if payload["lifecycle"] == "cancelled":
                return 130
            return 0 if payload["verdict"] == "pass" else 1

        if args.command == "diff":
            payload = compare_runs(args.baseline, args.candidate, args.max_p95_regression_percent)
            _emit(payload, args.json)
            return {"pass": 0, "fail": 1}.get(payload["verdict"], 2)

        bundle = load_scenario(args.scenario)
        manifest = bundle.manifest
        resolved_schedule = manifest.schedule.resolve(args.seed)
        payload = {
            "ok": True,
            "name": manifest.name,
            "schema_version": manifest.schema_version,
            "script": str(bundle.script_path),
            "planned_journeys": manifest.planned_journeys,
            "maximum_journey_requests": manifest.maximum_journey_requests,
            "maximum_journey_writes": manifest.maximum_journey_writes,
            "resolved_schedule": [phase.model_dump(exclude={"admitted_journeys"}) for phase in resolved_schedule],
            "assertions": manifest.assertions,
        }
        _emit(payload, args.json)
        return 0
    except (ComparisonError, RunnerError, ScenarioError, ValidationError, ValueError) as exc:
        _emit({"ok": False, "error": str(exc)}, getattr(args, "json", False))
        return 3


def entrypoint() -> None:
    raise SystemExit(main())
