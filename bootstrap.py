#!/usr/bin/env python3
"""
bootstrap.py — Obsidian Unified CRM Shadow Vault bootstrapper.

Runs BEFORE vault_crm_engine.py and entity_network_graph.py.
Responsibilities:
  - Verify prerequisites
  - Scaffold the minimum required vault structure
  - Log every action persistently to crm/_system/BootstrapLog.md

Usage:
    python bootstrap.py --ontology crm_ontology.yaml --vault ./vault [--quiet]

Exit codes:
    0 — READY or WARN  (engine may run)
    1 — ERROR          (blocking issue, do not run engine)
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from importlib import util as importlib_util
from pathlib import Path

# ---------------------------------------------------------------------------
# Log-entry constants
# ---------------------------------------------------------------------------
PASS    = "PASS   "
WARN    = "WARN   "
INFO    = "INFO   "
ERROR   = "ERROR  "
CREATED = "CREATED"
EXISTS  = "EXISTS "


# ---------------------------------------------------------------------------
# Log accumulator
# ---------------------------------------------------------------------------
class RunLog:
    def __init__(self):
        self.entries: list[tuple[str, str]] = []
        self.errors   = 0
        self.warnings = 0
        self.created  = 0

    def add(self, level: str, message: str):
        self.entries.append((level, message))
        if level == ERROR:
            self.errors += 1
        elif level == WARN:
            self.warnings += 1
        elif level == CREATED:
            self.created += 1

    @property
    def status(self) -> str:
        if self.errors:
            return "ERROR"
        if self.warnings:
            return "WARN"
        return "READY"


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------
def _fmt(level: str, message: str) -> str:
    return f"[{level}] {message}"


def emit(log: RunLog, level: str, message: str, quiet: bool = False):
    log.add(level, message)
    if not quiet:
        print(_fmt(level, message))


# ---------------------------------------------------------------------------
# Prerequisite checks
# ---------------------------------------------------------------------------
def check_vault_root(vault: Path, log: RunLog, quiet: bool):
    if vault.is_dir() and os.access(vault, os.W_OK):
        emit(log, PASS, f"Vault root exists and is writable: {vault}", quiet)
    elif not vault.is_dir():
        emit(log, ERROR, f"Vault root directory does not exist: {vault}", quiet)
    else:
        emit(log, ERROR, f"Vault root is not writable: {vault}", quiet)


def check_obsidian_dir(vault: Path, log: RunLog, quiet: bool):
    obsidian = vault / ".obsidian"
    if obsidian.is_dir():
        emit(log, PASS, ".obsidian/ directory found", quiet)
    else:
        emit(log, WARN, ".obsidian/ directory not found — open this folder in Obsidian first", quiet)


def check_app_json(vault: Path, log: RunLog, quiet: bool):
    app_json = vault / ".obsidian" / "app.json"
    if app_json.exists():
        emit(log, PASS, ".obsidian/app.json found", quiet)
        return
    obsidian_dir = vault / ".obsidian"
    if not obsidian_dir.is_dir():
        emit(log, INFO, ".obsidian/ missing — skipping app.json creation", quiet)
        return
    default_config = {
        "useMarkdownLinks": False,
        "newLinkFormat": "shortest",
        "attachmentFolderPath": "crm/_system/attachments",
    }
    app_json.write_text(json.dumps(default_config, indent=2) + "\n", encoding="utf-8")
    emit(log, CREATED, ".obsidian/app.json created with default CRM settings", quiet)


def check_ontology(ontology_path: Path, log: RunLog, quiet: bool) -> dict | None:
    if not ontology_path.exists():
        emit(log, ERROR, f"crm_ontology.yaml not found at path: {ontology_path}", quiet)
        return None
    try:
        import yaml  # noqa: PLC0415
        with ontology_path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
        if not isinstance(data, dict) or "ontology" not in data:
            emit(log, ERROR,
                 f"crm_ontology.yaml is invalid — missing top-level 'ontology' key: {ontology_path}",
                 quiet)
            return None
        emit(log, PASS, f"crm_ontology.yaml found and valid: {ontology_path}", quiet)
        return data
    except yaml.YAMLError as exc:
        emit(log, ERROR, f"crm_ontology.yaml YAML parse error: {exc}", quiet)
        return None
    except OSError as exc:
        emit(log, ERROR, f"crm_ontology.yaml could not be read: {exc}", quiet)
        return None


def check_python_deps(log: RunLog, quiet: bool):
    # yaml (pyyaml) — required
    if importlib_util.find_spec("yaml") is not None:
        emit(log, PASS, "Python dependency 'yaml' (pyyaml) is available", quiet)
    else:
        emit(log, ERROR, "Python dependency 'yaml' (pyyaml) is missing — run: pip install pyyaml", quiet)

    # rapidfuzz — optional
    if importlib_util.find_spec("rapidfuzz") is not None:
        emit(log, PASS, "Python dependency 'rapidfuzz' is available", quiet)
    else:
        emit(log, WARN,
             "Python dependency 'rapidfuzz' not found — fuzzy matching disabled (pip install rapidfuzz to enable)",
             quiet)


def check_plugins(vault: Path, log: RunLog, quiet: bool):
    plugins_dir = vault / ".obsidian" / "plugins"
    required_plugins = ["dataview", "folder-notes"]
    optional_plugins = ["waypoint", "obsidian-graph-analysis"]

    for plugin in required_plugins:
        if (plugins_dir / plugin).is_dir():
            emit(log, PASS, f"Plugin '{plugin}' found", quiet)
        else:
            emit(log, WARN,
                 f"Plugin '{plugin}' not found — install via Community Plugins in Obsidian",
                 quiet)

    for plugin in optional_plugins:
        if (plugins_dir / plugin).is_dir():
            emit(log, PASS, f"Optional plugin '{plugin}' found", quiet)
        else:
            emit(log, INFO, f"Plugin '{plugin}' not found — optional, skipped", quiet)


# ---------------------------------------------------------------------------
# Vault scaffold helpers
# ---------------------------------------------------------------------------
def _ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def _write_file_if_missing(path: Path, content: str, log: RunLog, quiet: bool, base: Path | None = None):
    rel = path.relative_to(base) if base and path.is_relative_to(base) else path
    if path.exists():
        emit(log, EXISTS, f"{rel} — skipped", quiet)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        emit(log, CREATED, str(rel), quiet)


# ---------------------------------------------------------------------------
# Scaffold: fixed structure
# ---------------------------------------------------------------------------
HOME_MD = """\
# Unified CRM — Shadow Vault

## Navigation
- [[Entities]]  — all entity definitions
- [[Teams]]     — all teams
- [[Views]]     — all views by entity/team
- [[Relationships]] — entity relationship map

## System
- [[MappingReport]] — column → canonical mapping results
- [[RunLog]]        — import history
- [[entity_network_analysis_report]] — network centrality

## Entities
- [[crm/Company/_index|Company]]
- [[crm/Contact/_index|Contact]]
- [[crm/Deal/_index|Deal]]
- [[crm/Communication/_index|Communication]]
"""

PHONE_YAML = """\
rules:
  default_country_code: "+41"
  strip_trunk_zero: true
  output_format: "E.164"
  validation: true
"""

LINKEDIN_YAML = """\
rules:
  person_anchor: "/in/"
  company_anchor: "/company/"
  strip_trailing_slash: true
  force_https: true
"""


def scaffold_fixed(vault: Path, log: RunLog, quiet: bool):
    # Directories
    for subdir in [
        "crm/_system",
        "crm/_ontology",
        "crm/_mappings",
        "crm/_rules",
    ]:
        _ensure_dir(vault / subdir)

    # Files
    _write_file_if_missing(vault / "crm/_system/Home.md",    HOME_MD,      log, quiet, vault)
    _write_file_if_missing(vault / "crm/_rules/phone.yaml",   PHONE_YAML,   log, quiet, vault)
    _write_file_if_missing(vault / "crm/_rules/linkedin.yaml", LINKEDIN_YAML, log, quiet, vault)


# ---------------------------------------------------------------------------
# Scaffold: entity directories (derived from ontology)
# ---------------------------------------------------------------------------
def scaffold_entities(vault: Path, ontology_data: dict, log: RunLog, quiet: bool):
    entities = ontology_data.get("ontology", {}).get("entities", {}) or {}
    teams_top = ontology_data.get("ontology", {}).get("teams", []) or []
    team_keys = [t["key"] for t in teams_top if isinstance(t, dict) and "key" in t]

    for entity_name in entities:
        entity_dir = vault / "crm" / entity_name
        _ensure_dir(entity_dir)

        # _index.md stub
        index_content = f"# {entity_name}\n\n%% Waypoint %%\n"
        _write_file_if_missing(entity_dir / "_index.md", index_content, log, quiet, vault)

        for team_key in team_keys:
            # _teams/<TeamName>/views/  — view scaffold dir
            _ensure_dir(entity_dir / "_teams" / team_key / "views")
            # <TeamName>/  — record dir per team
            _ensure_dir(entity_dir / team_key)


# ---------------------------------------------------------------------------
# Persistent run log
# ---------------------------------------------------------------------------
BOOTSTRAP_LOG_HEADER = "# Bootstrap Log\n"


def write_bootstrap_log(vault: Path, log: RunLog, timestamp: str):
    log_path = vault / "crm" / "_system" / "BootstrapLog.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        f"\n## Run: {timestamp}\n",
        "\n### Prerequisites + Scaffold\n",
    ]
    for level, message in log.entries:
        lines.append(f"- [{level}] {message}\n")
    lines.append("\n### Result\n")
    lines.append(f"- **Status**: {log.status}\n")
    lines.append(f"- Errors: {log.errors}\n")
    lines.append(f"- Warnings: {log.warnings}\n")
    lines.append(f"- Created: {log.created}\n")

    section = "".join(lines)

    if log_path.exists():
        existing = log_path.read_text(encoding="utf-8")
        # Ensure the header is present exactly once at the top
        if existing.startswith(BOOTSTRAP_LOG_HEADER):
            log_path.write_text(existing + section, encoding="utf-8")
        else:
            log_path.write_text(BOOTSTRAP_LOG_HEADER + existing + section, encoding="utf-8")
    else:
        log_path.write_text(BOOTSTRAP_LOG_HEADER + section, encoding="utf-8")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Bootstrap the Obsidian Unified CRM Shadow Vault.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--ontology",
        required=True,
        metavar="PATH",
        help="Path to crm_ontology.yaml",
    )
    parser.add_argument(
        "--vault",
        required=True,
        metavar="PATH",
        help="Path to Obsidian vault root",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        default=False,
        help="Suppress stdout; write only to BootstrapLog.md",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    vault = Path(args.vault).resolve()
    ontology_path = Path(args.ontology).resolve()
    quiet = args.quiet
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    log = RunLog()

    # ------------------------------------------------------------------
    # 1. Prerequisite checks
    # ------------------------------------------------------------------
    check_vault_root(vault, log, quiet)
    check_python_deps(log, quiet)

    # If vault root has errors already, bail early (can't write log either)
    if not vault.is_dir():
        print(_fmt(ERROR, "Cannot continue — vault root does not exist."))
        return 1

    check_obsidian_dir(vault, log, quiet)
    check_app_json(vault, log, quiet)
    check_plugins(vault, log, quiet)
    ontology_data = check_ontology(ontology_path, log, quiet)

    # ------------------------------------------------------------------
    # 2. Vault scaffold (only if vault is accessible)
    # ------------------------------------------------------------------
    scaffold_fixed(vault, log, quiet)
    if ontology_data is not None:
        scaffold_entities(vault, ontology_data, log, quiet)

    # ------------------------------------------------------------------
    # 3. Write persistent run log
    # ------------------------------------------------------------------
    write_bootstrap_log(vault, log, timestamp)

    # ------------------------------------------------------------------
    # 4. Summary line + exit code
    # ------------------------------------------------------------------
    if not quiet:
        print(f"\n[{'RESULT ':7}] Status: {log.status} | "
              f"Errors: {log.errors} | Warnings: {log.warnings} | Created: {log.created}")

    return 0 if log.status in ("READY", "WARN") else 1


if __name__ == "__main__":
    sys.exit(main())
