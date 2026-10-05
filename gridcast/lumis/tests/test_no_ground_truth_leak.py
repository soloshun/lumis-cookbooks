"""No file an investigator can read may name a scenario.

The 2026-10-05 tool-agent run found two leaks (research notes §7b): a docstring in an
allowlisted service labelled its fault "(scenario N)", and the tool agent's Git tools reached
the cookbook's own history, whose commit subjects and diffs describe the scenarios.
"""

import re
from pathlib import Path

import yaml

from gridcast_lumis.runner import GRIDCAST, PROJECT_FILE
from gridcast_lumis.tool_agent import HIDDEN, READABLE

# Scenario letters, the injector and its state directory. ("Ground-truth weather" is a domain
# term in the weather simulator, which is hidden anyway.)
LABEL = re.compile(r"(?i)\bscenarios?\s+[A-O]\b|\bchaos\b|\.gridcast/chaos|failure injector")


def _lumis_files() -> list[Path]:
    project = yaml.safe_load(PROJECT_FILE.read_text())
    files = []
    for repo in project["investigator"]["repositories"]:
        root = (PROJECT_FILE.parent / repo["root"]).resolve()
        files += [root / name for name in repo["files"] if (root / name).is_file()]
    return files


def _tool_agent_files() -> list[Path]:
    files = []
    for prefix in READABLE:
        for path in (GRIDCAST / prefix).rglob("*"):
            rel = path.relative_to(GRIDCAST).as_posix()
            if path.is_file() and not rel.startswith(HIDDEN) and ".env" not in rel and "__pycache__" not in rel:
                files.append(path)
    return files


def _leaks(files: list[Path]) -> list[str]:
    found = []
    for path in files:
        text = path.read_text(errors="ignore")
        found += [f"{path.relative_to(GRIDCAST.parent)}: {m.group(0)!r}" for m in LABEL.finditer(text)]
    return found


def test_lumis_allowlist_names_no_scenario() -> None:
    assert _lumis_files(), "allowlist resolved to no files"
    assert _leaks(_lumis_files()) == []


def test_tool_agent_readable_files_name_no_scenario() -> None:
    assert _leaks(_tool_agent_files()) == []
