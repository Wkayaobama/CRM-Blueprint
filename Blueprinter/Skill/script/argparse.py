docstring:
```python
This sample focuses on the foundation pipeline:

load schema
ingest CSVs
map CSV columns → canonical props (exact+normalized+fuzzy)
write record notes + companion notes
write mapping report
```
# name=vault_crm_foundation.py
from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml  # pip install pyyaml

try:
    from rapidfuzz import fuzz, process  # type: ignore
    HAS_RAPIDFUZZ = True
except Exception:
    import difflib
    HAS_RAPIDFUZZ = False

Json = Dict[str, Any]


# ----------------- small utilities -----------------

def slugify(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9\-]+", "", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "record"


def norm(s: str) -> str:
    """Normalize for matching: lower, underscore-ish."""
    s = (s or "").strip().lower()
    s = re.sub(r"[\s\-]+", "_", s)
    s = re.sub(r"[^a-z0-9_]", "", s)
    s = re.sub(r"_{2,}", "_", s).strip("_")
    return s


def fuzzy_best(query: str, choices: List[str], *, min_score: float) -> Tuple[Optional[str], float]:
    q = norm(query)
    if not q or not choices:
        return None, 0.0

    if HAS_RAPIDFUZZ:
        res = process.extractOne(q, choices, scorer=fuzz.WRatio)
        if not res:
            return None, 0.0
        choice, score, _idx = res
        return (choice if float(score) >= min_score else None), float(score)

    close = difflib.get_close_matches(q, choices, n=1, cutoff=min_score / 100.0)
    if not close:
        return None, 0.0
    return close[0], 100.0


# ----------------- schema model -----------------

@dataclass(frozen=True)
class ViewDef:
    name: str
    display_properties: List[str]


@dataclass(frozen=True)
class TeamDef:
    name: str
    views: List[ViewDef]


@dataclass(frozen=True)
class EntityDef:
    name: str
    properties: Dict[str, Dict[str, Any]]  # canonical property -> metadata dict
    teams: Dict[str, TeamDef]
    id_property: str  # unique+required identity property


@dataclass(frozen=True)
class CanonicalModel:
    crm_name: str
    entities: Dict[str, EntityDef]


def load_canonical_model(schema_path: Path) -> CanonicalModel:
    y = yaml.safe_load(schema_path.read_text(encoding="utf-8"))
    crm = y["crm"]
    crm_name = str(crm.get("name") or "CRM")

    entities: Dict[str, EntityDef] = {}
    for entity_name, ent in (crm.get("entities") or {}).items():
        props: Dict[str, Dict[str, Any]] = ent.get("properties") or {}

        # choose identity prop: first required+unique, else error
        id_prop = None
        for k, meta in props.items():
            if isinstance(meta, dict) and meta.get("required") and meta.get("unique"):
                id_prop = str(k)
                break
        if not id_prop:
            raise ValueError(f"Entity {entity_name} has no required+unique property to use as id")

        teams: Dict[str, TeamDef] = {}
        for t in (ent.get("teams") or []):
            tname = str(t.get("name") or "").strip()
            if not tname:
                continue
            views = [
                ViewDef(name=str(v["name"]), display_properties=list(v.get("display_properties") or []))
                for v in (t.get("views") or [])
            ]
            teams[tname] = TeamDef(name=tname, views=views)

        entities[entity_name] = EntityDef(
            name=entity_name,
            properties=props,
            teams=teams,
            id_property=id_prop,
        )

    return CanonicalModel(crm_name=crm_name, entities=entities)


# ----------------- filename inference -----------------

@dataclass(frozen=True)
class FileHint:
    team: str
    entity: str


FILENAME_RE = re.compile(r"(?P<team>[^_]+)__?(?P<entity>Company|Contact|Deal|Communication)\.csv$", re.IGNORECASE)

def infer_from_filename(path: Path) -> FileHint:
    m = FILENAME_RE.search(path.name)
    if not m:
        raise ValueError(
            f"Cannot infer team/entity from filename {path.name!r}. "
            f"Expected e.g. IcAlps__Contact.csv"
        )
    team = m.group("team")
    entity = m.group("entity")
    # normalize entity capitalization to schema keys
    entity = entity[0].upper() + entity[1:].lower()
    return FileHint(team=team, entity=entity)


# ----------------- CSV ingestion -----------------

def iter_csv_rows(path: Path) -> Iterable[Json]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # keep as strings; empty -> None
            clean: Json = {}
            for k, v in row.items():
                if k is None:
                    continue
                vv = v.strip() if isinstance(v, str) else v
                clean[k.strip()] = vv if vv != "" else None
            yield clean


# ----------------- mapping -----------------

@dataclass(frozen=True)
class MapLine:
    crm: str
    entity: str
    source_field: str
    canonical_field: Optional[str]
    score: float

    def format(self) -> str:
        return f"{self.crm} | {self.entity} | {self.source_field} | {self.canonical_field or '?'}"


def build_column_mapping(
    entity_def: EntityDef,
    source_columns: List[str],
    *,
    min_score: float,
    overrides: Dict[str, str],
) -> Tuple[Dict[str, str], List[MapLine]]:
    """
    Returns:
      - col->canonical mapping for matched columns
      - report lines for all columns (matched or not)
    """
    canonical_keys = list(entity_def.properties.keys())

    # normalized vocab for matching
    canon_norm = [norm(x) for x in canonical_keys]
    norm_to_canon: Dict[str, str] = {}
    for k in canonical_keys:
        norm_to_canon.setdefault(norm(k), k)

    mapping: Dict[str, str] = {}
    report: List[MapLine] = []

    for col in source_columns:
        # overrides win (override file uses raw column header as key)
        if col in overrides:
            canonical = overrides[col]
            mapping[col] = canonical
            report.append(MapLine("", entity_def.name, col, canonical, 100.0))
            continue

        # exact normalized match
        ncol = norm(col)
        if ncol in norm_to_canon:
            canonical = norm_to_canon[ncol]
            mapping[col] = canonical
            report.append(MapLine("", entity_def.name, col, canonical, 100.0))
            continue

        # fuzzy
        best_norm, score = fuzzy_best(col, canon_norm, min_score=min_score)
        if best_norm:
            canonical = norm_to_canon.get(best_norm, best_norm)
            mapping[col] = canonical
            report.append(MapLine("", entity_def.name, col, canonical, score))
        else:
            report.append(MapLine("", entity_def.name, col, None, score))

    return mapping, report


def load_overrides(path: Optional[Path]) -> Dict[str, Dict[str, str]]:
    """
    Optional overrides YAML structure:
    overrides:
      Contact:
        "First Name": firstname
        "Phone Number": mobile
      Company:
        "Company name": name
    """
    if not path:
        return {}
    y = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return (y.get("overrides") or {})


# ----------------- vault writing -----------------

def crm_key(crm_name: str, entity: str, identity: str) -> str:
    return f"{crm_name}|{entity}|{identity}".lower()


def write_record_note(
    vault: Path,
    model: CanonicalModel,
    entity_def: EntityDef,
    *,
    team: str,
    canonical_record: Json,
) -> Path:
    entity = entity_def.name
    crm_name = model.crm_name

    identity = canonical_record.get(entity_def.id_property)
    if not identity:
        raise ValueError(f"Record missing identity field {entity_def.id_property!r} for entity {entity!r}")

    identity_s = str(identity).strip()
    key = crm_key(crm_name, entity, identity_s)

    title = canonical_record.get("name") or canonical_record.get("dealname") or identity_s
    file_base = slugify(str(title))

    target = vault / "crm" / entity / team / f"{file_base}.md"
    target.parent.mkdir(parents=True, exist_ok=True)

    # canonical frontmatter only (+ envelope)
    fm: Json = {
        "crm_system": crm_name,
        "crm_entity": entity,
        "crm_team": team,
        "crm_key": key,
    }
    for canon_prop in entity_def.properties.keys():
        if canon_prop in canonical_record and canonical_record[canon_prop] is not None:
            fm[canon_prop] = canonical_record[canon_prop]

    body = "\n".join([
        "---",
        yaml.safe_dump(fm, sort_keys=False).strip(),
        "---",
        "",
        f"# {title}",
        "",
        f"- Companion notes: [[{target.with_suffix('').name}.notes]]",
        "",
    ])
    target.write_text(body, encoding="utf-8")
    return target


def ensure_companion_note(record_path: Path) -> Path:
    companion = record_path.with_name(record_path.stem + ".notes.md")
    if companion.exists():
        return companion
    companion.write_text("\n".join([
        "# Notes",
        "",
        "Personal notes/comments for this record. This file is never overwritten by the generator.",
        "",
    ]), encoding="utf-8")
    return companion


def write_view_notes(vault: Path, model: CanonicalModel) -> None:
    for entity_def in model.entities.values():
        for team_name, team_def in entity_def.teams.items():
            for view in team_def.views:
                path = vault / "crm" / entity_def.name / "_teams" / team_name / "views" / f"{view.name}.md"
                path.parent.mkdir(parents=True, exist_ok=True)
                fm = {
                    "crm_system": model.crm_name,
                    "crm_entity": entity_def.name,
                    "crm_team": team_name,
                    "crm_view": view.name,
                    "display_properties": view.display_properties,
                }
                content = "\n".join([
                    "---",
                    yaml.safe_dump(fm, sort_keys=False).strip(),
                    "---",
                    "",
                    f"# {view.name}",
                    "",
                    "## Records (static)",
                    "",
                    "> To be materialized (table of links + display_properties).",
                    "",
                    "## Records (dynamic / optional)",
                    "",
                    "> Optional Dataview query can be inserted here later.",
                    "",
                ])
                path.write_text(content, encoding="utf-8")


def write_mapping_report(vault: Path, model: CanonicalModel, report_lines: List[str]) -> None:
    path = vault / "crm" / "_system" / "MappingReport.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    header = "\n".join([
        f"# Mapping Report ({model.crm_name})",
        "",
        "Format: `CRM | Entity | Source Field | Canonical Field`",
        "",
        "Unresolved matches show `?`.",
        "",
        "```",
    ])
    footer = "\n``` \n"
    path.write_text(header + "\n" + "\n".join(report_lines) + "\n" + footer, encoding="utf-8")


# ----------------- main CLI -----------------

def main() -> int:
    ap = argparse.ArgumentParser("vaultgen", description="Generate an Obsidian shadow CRM foundation from CSV + canonical YAML")
    ap.add_argument("--schema", required=True, help="Canonical schema YAML")
    ap.add_argument("--vault", required=True, help="Path to Obsidian vault root")
    ap.add_argument("--csv", nargs="+", required=True, help="CSV export files (e.g. IcAlps__Contact.csv)")
    ap.add_argument("--overrides", help="Optional mapping overrides YAML")
    ap.add_argument("--min-score", type=float, default=85.0, help="Fuzzy matching threshold for column->canonical mapping")
    args = ap.parse_args()

    vault = Path(args.vault)
    model = load_canonical_model(Path(args.schema))
    overrides_by_entity = load_overrides(Path(args.overrides) if args.overrides else None)

    # write view scaffolding
    write_view_notes(vault, model)

    mapping_report: List[str] = []

    for csv_path in map(Path, args.csv):
        hint = infer_from_filename(csv_path)
        entity_def = model.entities.get(hint.entity)
        if not entity_def:
            raise ValueError(f"Entity {hint.entity!r} from filename not in schema")

        team = hint.team
        if team not in entity_def.teams:
            # not fatal, but signals schema/team mismatch
            raise ValueError(f"Team {team!r} not defined in schema for entity {hint.entity!r}")

        # read one row to get columns
        rows = list(iter_csv_rows(csv_path))
        if not rows:
            continue
        source_columns = list(rows[0].keys())

        col_overrides = overrides_by_entity.get(hint.entity, {})
        col_to_canon, rep = build_column_mapping(
            entity_def,
            source_columns,
            min_score=args.min_score,
            overrides=col_overrides,
        )

        # finalize report lines with CRM name
        for line in rep:
            mapping_report.append(
                f"{model.crm_name} | {hint.entity} | {line.source_field} | {line.canonical_field or '?'}"
            )

        # transform rows -> canonical records and write notes
        for row in rows:
            canonical: Json = {}
            for src_col, value in row.items():
                if value is None:
                    continue
                canon = col_to_canon.get(src_col)
                if canon:
                    canonical[canon] = value

            # enforce team: one record -> one team (from filename)
            # (we keep team outside canonical fields)
            record_path = write_record_note(vault, model, entity_def, team=team, canonical_record=canonical)
            ensure_companion_note(record_path)

    write_mapping_report(vault, model, mapping_report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())