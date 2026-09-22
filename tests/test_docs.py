import re
from pathlib import Path

from litetraffic.cli import _parser

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
    known = set().union(*(_flags(command) for command in _commands().values()))
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
