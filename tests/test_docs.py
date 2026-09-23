import re
from pathlib import Path

import pytest

from litetraffic.cli import _parser, main

ROOT = Path(__file__).resolve().parents[1]
CLI_DOC = (ROOT / "docs" / "cli.md").read_text()
FLAG = re.compile(r"(?<![\w-])--[a-z][a-z0-9-]*")


def _commands() -> dict:
    return _parser()._subparsers._group_actions[0].choices


def _flags(command) -> set[str]:
    return {flag for action in command._actions for flag in action.option_strings if flag.startswith("--")}


def _sections() -> dict[str, str]:
    parts = re.split(r"^## `([a-z]+)`$", CLI_DOC, flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


def test_cli_doc_documents_exactly_the_parser_subcommands():
    assert set(_sections()) == set(_commands())


def test_cli_doc_synopsis_flags_match_each_parser():
    for name, body in _sections().items():
        synopsis = re.search(r"```\w*\n(.*?)```", body, flags=re.S).group(1)
        assert set(FLAG.findall(synopsis)) == _flags(_commands()[name]) - {"--help"}, name


def test_every_flag_in_cli_doc_exists_in_the_parser():
    known = set().union(_flags(_parser()), *(_flags(command) for command in _commands().values()))
    # --host is documented as absent from dashboard; --max-redirects is passed to k6.
    assert set(FLAG.findall(CLI_DOC)) - known == {"--host", "--max-redirects"}


def test_readme_and_roadmap_name_every_subcommand():
    readme = (ROOT / "README.md").read_text()
    preview = next(line for line in readme.splitlines() if line.startswith("> **Developer preview"))
    roadmap = (ROOT / "docs" / "roadmap.md").read_text()
    available = roadmap.split("## Available now", 1)[1].split("\n## ", 1)[0]
    for name in _commands():
        assert f"`{name}`" in preview, name
        assert f"`{name}`" in available, name


def test_readme_does_not_claim_concurrency_is_checked_before_running():
    readme = (ROOT / "README.md").read_text()
    assert "concurrency budgets before running" not in readme


def test_every_help_flag_appears_in_its_cli_doc_section(capsys):
    sections = _sections()
    for name in _commands():
        with pytest.raises(SystemExit):
            main([name, "--help"])
        help_flags = set(FLAG.findall(capsys.readouterr().out)) - {"--help"}
        missing = help_flags - set(FLAG.findall(sections[name]))
        assert not missing, (name, missing)


def test_cli_doc_lists_every_command_taking_the_scenario_flag():
    intro = CLI_DOC.split("\n## ", 1)[0]
    for name, command in _commands().items():
        if "--scenario" in _flags(command):
            assert f"`{name}`" in intro, name


def test_cli_doc_states_the_dashboard_default_port():
    port = next(a.default for a in _commands()["dashboard"]._actions if "--port" in a.option_strings)
    assert f"default port `{port}`" in _sections()["dashboard"]


# Manifest features shipped after the first docs sweep; the README feature list and the roadmap's
# "Available now" must name each one.
SHIPPED = ["fixtures.command", "fixtures.pool", "allowed_origins", "${planned_journeys}", "auth", "min_overlap", "expected_statuses", "per_identity", "until"]


def test_readme_feature_list_and_roadmap_name_shipped_manifest_features():
    readme = (ROOT / "README.md").read_text()
    features = readme.split("## What it does", 1)[1].split("\n## ", 1)[0]
    roadmap = (ROOT / "docs" / "roadmap.md").read_text()
    available = roadmap.split("## Available now", 1)[1].split("\n## ", 1)[0]
    for name in SHIPPED:
        assert f"`{name}`" in features, name
        assert f"`{name}`" in available, name
    assert "same-origin HTTP observer" not in features


def test_second_origin_docs_name_every_header_sent_there():
    # observation.py sends the run/fixture headers, the bearer_token_env token and headers_env values.
    safety = (ROOT / "docs" / "safety.md").read_text()
    bullet = safety.split("**A second origin is opt-in.**", 1)[1].split("\n", 1)[0]
    scenarios = (ROOT / "docs" / "scenarios.md").read_text()
    origin_notes = scenarios.split("- `origin` must be", 1)[1].split("\n## ", 1)[0]
    for text in (bullet, origin_notes):
        assert "`bearer_token_env`" in text
        assert "`headers_env`" in text


def _docs() -> dict[str, str]:
    paths = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
    return {path.name if path.parent == ROOT else f"docs/{path.name}": path.read_text() for path in paths}


def test_overlap_is_called_a_client_side_check_not_a_concurrency_proof():
    docs = _docs()
    for name, text in docs.items():
        assert "Concurrency proof" not in text, name
    for name in ("README.md", "docs/roadmap.md", "docs/results.md", "docs/scenarios.md"):
        assert "client-side overlap check" in docs[name], name
        assert "not that the server executed them concurrently" in docs[name], name


def test_budgets_are_described_as_declared_bounds_not_containment():
    docs = _docs()
    for name in ("README.md", "docs/safety.md"):
        assert "declared bounds checked before and after the run, not hard containment" in docs[name], name
        assert "`up` budgets apply per slice" in docs[name], name


def test_readme_leads_with_the_present_tense_promise_and_roadmap_keeps_the_vision():
    docs = _docs()
    readme = docs["README.md"]
    lead = readme.split("\n\n", 1)[1].lstrip()
    assert lead.startswith(
        "**Verify critical API invariants against a running app, with repeatable scenarios and inspectable evidence.**"
    )
    assert "users as an API" not in readme
    assert "users as an API" in docs["docs/roadmap.md"]
