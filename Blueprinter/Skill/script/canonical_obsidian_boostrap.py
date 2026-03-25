
FILENAME_RE = re.compile(r"(?P<team>[^_]+)__?(?P<entity>Company|Contact|Deal|Communication)\.csv$", re.IGNORECASE)

@dataclass(frozen=True)
class FileHint:
    team: str
    entity: str

def infer_from_filename(path: Path) -> FileHint:
    m = FILENAME_RE.search(path.name)
    if not m:
        raise ValueError(f"Bad filename {path.name!r}. Expected e.g. IcAlps__Contact.csv")
    team = m.group("team")
    entity = m.group("entity")
    entity = entity[0].upper() + entity[1:].lower()
    return FileHint(team=team, entity=entity)


@dataclass
class RawBatch:
    team: str
    entity: str
    headers: List[str]
    rows: Iterable[Json]  # streaming iterable


def read_csv_stream(path: Path) -> Tuple[List[str], Iterable[Json]]:
    f = path.open("r", encoding="utf-8-sig", newline="")
    reader = csv.DictReader(f)
    headers = reader.fieldnames or []

    def gen() -> Iterable[Json]:
        try:
            for row in reader:
                clean: Json = {}
                for k, v in row.items():
                    if k is None:
                        continue
                    vv = v.strip() if isinstance(v, str) else v
                    clean[k.strip()] = vv if vv != "" else None
                yield clean
        finally:
            f.close()

    return headers, gen()


@dataclass
class MappingResult:
    entity: str
    team: str
    col_to_canon: Dict[str, str]
    report_lines: List[str]


@dataclass
class PreparedRecord:
    entity: str
    team: str
    crm_key: str
    title: str
    canonical: Json


@dataclass
class Engine:
    model: CanonicalModel
    overrides: Dict[str, Dict[str, str]]
    min_score: float

    def index(self, csv_paths: List[Path]) -> List[RawBatch]:
        batches: List[RawBatch] = []
        for p in csv_paths:
            hint = infer_from_filename(p)
            headers, rows = read_csv_stream(p)
            batches.append(RawBatch(team=hint.team, entity=hint.entity, headers=headers, rows=rows))
        return batches

    def prepare_mapping(self, batch: RawBatch) -> MappingResult:
        ent = self.model.entities[batch.entity]
        overrides = self.overrides.get(batch.entity, {})

        canonical_keys = list(ent.properties.keys())
        canon_norm = [norm(x) for x in canonical_keys]
        norm_to_canon: Dict[str, str] = {norm(k): k for k in canonical_keys}

        col_to_canon: Dict[str, str] = {}
        report: List[str] = []

        for col in batch.headers:
            if col in overrides:
                canon = overrides[col]
                col_to_canon[col] = canon
                report.append(f"{self.model.crm_name} | {batch.entity} | {col} | {canon}")
                continue

            ncol = norm(col)
            if ncol in norm_to_canon:
                canon = norm_to_canon[ncol]
                col_to_canon[col] = canon
                report.append(f"{self.model.crm_name} | {batch.entity} | {col} | {canon}")
                continue

            best_norm, _score = fuzzy_best(col, canon_norm, min_score=self.min_score)
            if best_norm:
                canon = norm_to_canon[best_norm]
                col_to_canon[col] = canon
                report.append(f"{self.model.crm_name} | {batch.entity} | {col} | {canon}")
            else:
                report.append(f"{self.model.crm_name} | {batch.entity} | {col} | ?")

        return MappingResult(batch.entity, batch.team, col_to_canon, report)

    def prepare_records(self, batch: RawBatch, mapping: MappingResult) -> Iterable[PreparedRecord]:
        ent = self.model.entities[batch.entity]
        crm_name = self.model.crm_name

        for row in batch.rows:
            canonical: Json = {}
            for src_col, value in row.items():
                if value is None:
                    continue
                canon = mapping.col_to_canon.get(src_col)
                if canon:
                    canonical[canon] = value

            # minimal “prepare transforms” (example: rename FK fields to association names)
            # You can formalize this based on schema later.
            if batch.entity == "Contact" and "icalps_company_id" in canonical and "company_association" not in canonical:
                canonical["company_association"] = canonical["icalps_company_id"]
            if batch.entity == "Deal":
                if "icalps_company_id" in canonical and "company_association" not in canonical:
                    canonical["company_association"] = canonical["icalps_company_id"]
                if "icalps_contact_id" in canonical and "contact_association" not in canonical:
                    canonical["contact_association"] = canonical["icalps_contact_id"]

            identity = canonical.get(ent.id_property)
            if not identity:
                # skip invalid record; could log
                continue

            identity_s = str(identity).strip()
            crm_key = f"{crm_name}|{batch.entity}|{identity_s}".lower()

            title = str(canonical.get("name") or canonical.get("dealname") or identity_s).strip()
            yield PreparedRecord(batch.entity, batch.team, crm_key, title, canonical)

    def render(self, layout: VaultLayout, prepared: PreparedRecord) -> None:
        ent = self.model.entities[prepared.entity]

        record_dir = layout.team_record_dir(prepared.entity, prepared.team)
        record_dir.mkdir(parents=True, exist_ok=True)

        base = slugify(prepared.title)
        record_path = record_dir / f"{base}.md"
        notes_path = record_dir / f"{base}.notes.md"

        fm: Json = {
            "crm_system": self.model.crm_name,
            "crm_entity": prepared.entity,
            "crm_team": prepared.team,
            "crm_key": prepared.crm_key,
        }
        # only canonical schema keys
        for k in ent.properties.keys():
            if k in prepared.canonical and prepared.canonical[k] is not None:
                fm[k] = prepared.canonical[k]

        record_path.write_text("\n".join([
            "---",
            yaml.safe_dump(fm, sort_keys=False).strip(),
            "---",
            "",
            f"# {prepared.title}",
            "",
            f"- Notes: [[{notes_path.stem}]]",
            "",
        ]), encoding="utf-8")

        if not notes_path.exists():
            notes_path.write_text("\n".join([
                "# Notes",
                "",
                "User notes/comments. Never overwritten by generator.",
                "",
            ]), encoding="utf-8")


# ----------------- CLI -----------------

def append_runlog(layout: VaultLayout, model: CanonicalModel, msg: str) -> None:
    path = layout.system_dir / "RunLog.md"
    ts = dt.datetime.now().isoformat(timespec="seconds")
    path.write_text(path.read_text(encoding="utf-8") + f"- {ts} {msg}\n", encoding="utf-8")


def write_mapping_report(layout: VaultLayout, model: CanonicalModel, lines: List[str]) -> None:
    path = layout.system_dir / "MappingReport.md"
    body = "\n".join([
        f"# Mapping Report ({model.crm_name})",
        "",
        "```",
        *lines,
        "```",
        "",
    ])
    path.write_text(body, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser("vaultgen", description="Index → Prepare → Render engine for Obsidian shadow CRM")
    ap.add_argument("--schema", required=True)
    ap.add_argument("--vault", required=True)
    ap.add_argument("--csv", nargs="+", required=True)
    ap.add_argument("--overrides", help="Optional overrides YAML")
    ap.add_argument("--min-score", type=float, default=85.0)
    args = ap.parse_args()

    model = load_canonical_model(Path(args.schema))
    overrides = load_overrides(Path(args.overrides) if args.overrides else None)

    layout = VaultLayout(Path(args.vault))
    layout.bootstrap(model)

    engine = Engine(model=model, overrides=overrides, min_score=args.min_score)

    batches = engine.index([Path(p) for p in args.csv])

    all_report_lines: List[str] = []
    count = 0

    for batch in batches:
        mapping = engine.prepare_mapping(batch)
        all_report_lines.extend(mapping.report_lines)

        for rec in engine.prepare_records(batch, mapping):
            engine.render(layout, rec)
            count += 1

    write_mapping_report(layout, model, all_report_lines)
    append_runlog(layout, model, f"Imported {count} records from {len(batches)} CSV file(s).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())