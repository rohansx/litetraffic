"""GitHub filing for `report issue`, through the `gh` CLI only (subprocess, never a shell)."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from litetraffic.issue import IssueError

TIMEOUT_SECONDS = 60
WRITE_PERMISSIONS = frozenset({"ADMIN", "MAINTAIN", "WRITE"})
ISSUE_URL_RE = re.compile(r"https://\S+/issues/\d+(?:#\S*)?")
INSTALL = "gh not found. Install GitHub CLI (https://cli.github.com) and run `gh auth login`."
UNAUTHENTICATED = "gh is not authenticated: run `gh auth login` or set GH_TOKEN."


class GhError(IssueError):
    """A gh call failed; the message is already scrubbed."""


class Gh:
    """Runs gh with an explicit repo on every call: GH_REPO is dropped so gh can never infer one."""

    def __init__(self, scrub: Callable[[str], str] = lambda text: text) -> None:
        self.scrub = scrub
        self.path = shutil.which("gh")
        if self.path is None:
            raise GhError(INSTALL)
        env = {key: value for key, value in os.environ.items() if key != "GH_REPO"}
        self.env = {**env, "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1", "NO_COLOR": "1"}

    def run(self, *argv: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                [self.path, *argv], cwd=cwd, env=self.env, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=TIMEOUT_SECONDS, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GhError(f"gh {argv[0]} timed out after {TIMEOUT_SECONDS}s") from exc
        except OSError as exc:
            raise GhError(f"could not run gh: {exc}") from exc

    def check(self, *argv: str, cwd: Path | None = None) -> str:
        done = self.run(*argv, cwd=cwd)
        if done.returncode != 0:
            raise GhError(f"gh {' '.join(argv[:2])} failed: {self.error(done)}")
        return done.stdout

    def error(self, done: subprocess.CompletedProcess) -> str:
        text = (done.stderr or done.stdout or "").strip() or f"exit {done.returncode}"
        return self.scrub(text)

    def ensure_auth(self) -> None:
        if self.run("auth", "status", "--hostname", "github.com").returncode != 0:
            raise GhError(UNAUTHENTICATED)

    def login(self) -> str:
        return self.check("api", "user", "-q", ".login").strip()

    def find_duplicates(self, repo: str, fingerprint: str) -> list[dict]:
        """Open issues whose body carries the fingerprint: search, plus the user's own list for search-index lag."""
        found: dict[str, dict] = {}
        search = self.check("search", "issues", fingerprint, "-R", repo, "--state", "open", "--match", "body",
                            "--json", "number,url,body", "-L", "20")
        listing = self.check("api", "-X", "GET", f"repos/{repo}/issues", "-f", "state=open", "-f", f"creator={self.login()}",
                             "-f", "per_page=100", "--paginate", "-q", ".[] | {number, url: .html_url, body, pull_request}")
        for item in [*_json_items(search), *_json_items(listing)]:
            url = item.get("url")
            if isinstance(url, str) and carries_fingerprint(str(item.get("body") or ""), fingerprint) and not item.get("pull_request"):
                found.setdefault(url, {"number": item.get("number"), "url": url})
        return list(found.values())

    def can_attach(self, repo: str) -> tuple[bool, str]:
        """gh uploads attachments only with --attach support, a non-installation token and WRITE+ on the repo."""
        if "--attach" not in self.run("issue", "create", "--help").stdout:
            return False, "this gh has no --attach (gh 2.99 or later uploads videos)"
        if any(os.environ.get(name, "").startswith("ghs_") for name in ("GH_TOKEN", "GITHUB_TOKEN")):
            return False, "gh cannot upload attachments with an installation token (ghs_)"
        done = self.run("repo", "view", repo, "--json", "viewerPermission", "-q", ".viewerPermission")
        permission = done.stdout.strip().upper() if done.returncode == 0 else ""
        if permission not in WRITE_PERMISSIONS:
            return False, f"uploading needs write access to {repo} (yours: {permission or 'unknown'})"
        return True, "ok"

    def _filed(self, argv: list[str], cwd: Path) -> tuple[str, str | None]:
        """The issue URL even when gh exits non-zero after creating it (a failed upload); otherwise raise."""
        done = self.run(*argv, cwd=cwd)
        match = ISSUE_URL_RE.search(done.stdout or "")
        if match is None:
            raise GhError(f"gh {' '.join(argv[:2])} failed: {self.error(done)}")
        return match.group(0), (self.error(done) if done.returncode != 0 else None)

    def create_issue(self, repo: str, title: str, body: Path, labels: list[str], attach: list[str]) -> tuple[str, str | None]:
        argv = ["issue", "create", "-R", repo, "-t", title, "-F", str(body)]
        argv += [token for label in labels for token in ("-l", label)]
        argv += [token for name in attach for token in ("--attach", name)]
        return self._filed(argv, body.parent)

    def comment_issue(self, url: str, body: Path) -> str:
        filed, error = self._filed(["issue", "comment", url, "-F", str(body)], body.parent)
        if error:
            raise GhError(f"gh issue comment failed: {error}")
        return filed

    def issue_state(self, url: str) -> str:
        """OPEN or CLOSED; anything else (a failed lookup included) reads as UNKNOWN, which callers treat as open."""
        done = self.run("issue", "view", url, "--json", "state", "-q", ".state")
        state = done.stdout.strip().upper() if done.returncode == 0 else ""
        return state if state in ("OPEN", "CLOSED") else "UNKNOWN"

    def edit_issue(self, url: str, body: Path) -> None:
        self.check("issue", "edit", url, "-F", str(body), cwd=body.parent)

    def pvr_enabled(self, repo: str) -> bool:
        return self.check("api", f"repos/{repo}/private-vulnerability-reporting", "-q", ".enabled").strip() == "true"

    def report_vulnerability(self, repo: str, payload: Path) -> str:
        out = self.check("api", "-X", "POST", f"repos/{repo}/security-advisories/reports", "--input", str(payload))
        try:
            url = json.loads(out).get("html_url")
        except (ValueError, AttributeError):
            url = None
        if not isinstance(url, str):
            raise GhError("gh api returned no advisory URL")
        return url


def carries_fingerprint(body: str, fingerprint: str) -> bool:
    """The fingerprint on a line of its own, as the tool writes it (the leading HTML comment or the footer), never mid-text.

    Values from the target are rendered inline and cannot start a line, so a response body cannot plant one.
    """
    marker = re.compile(rf"^(?:<!-- |Reported with LiteTraffic \S+ · )?litetraffic-fingerprint: {re.escape(fingerprint)}(?: -->)?\s*$",
                        re.MULTILINE)
    return marker.search(body) is not None


def _json_items(text: str) -> list[dict]:
    """A JSON array, or one JSON object per line (gh api --paginate -q)."""
    text = text.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
        items = data if isinstance(data, list) else [data]
    except ValueError:
        items = []
        for line in text.splitlines():
            try:
                items.append(json.loads(line))
            except ValueError:
                continue
    return [item for item in items if isinstance(item, dict)]
