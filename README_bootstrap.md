# Bootstrap Script — Obsidian Unified CRM Shadow Vault

`bootstrap.py` prepares the vault before the ingestion engine runs.
It verifies prerequisites, scaffolds the required directory and file structure,
and writes a persistent log of every action it takes.

---

## 1. Install prerequisites

```bash
pip install pyyaml          # required — YAML parsing
pip install rapidfuzz       # optional — enables fuzzy column mapping in the engine
```

---

## 2. Three-command sequence

Run the three scripts in this order every time you import new data.

### Step 1 — Bootstrap (this script)

```bash
python bootstrap.py --ontology crm_ontology.yaml --vault ./vault
```

| Flag | Required | Description |
|------|----------|-------------|
| `--ontology` | ✅ | Path to `crm_ontology.yaml` |
| `--vault` | ✅ | Path to the Obsidian vault root folder |
| `--quiet` | ☐ | Suppress stdout; write only to `BootstrapLog.md` |

**Exit codes**

| Code | Meaning |
|------|---------|
| `0` | `READY` or `WARN` — engine may run |
| `1` | `ERROR` — blocking issue; do **not** run engine |

### Step 2 — Ingestion engine

```bash
python vault_crm_engine.py --ontology crm_ontology.yaml --vault ./vault --input ./data/
```

Reads source CSV/MySQL/Kafka data, maps columns to canonical properties, and
writes record notes into the vault under the entity/team hierarchy.

### Step 3 — Network graph

```bash
python entity_network_graph.py --ontology crm_ontology.yaml --vault ./vault
```

Analyses entity relationships, computes centrality metrics, and writes:
- `crm/_system/entity_network_metrics.csv`
- `crm/_system/entity_network_analysis_report.txt`

---

## 3. Vault structure after all three steps

```
vault/
├── .obsidian/
│   ├── app.json                   ← created by bootstrap if missing
│   └── plugins/
│       ├── dataview/
│       ├── folder-notes/
│       ├── waypoint/
│       └── obsidian-graph-analysis/
└── crm/
    ├── _system/
    │   ├── Home.md                ← navigation hub
    │   ├── BootstrapLog.md        ← persistent run log (appended each run)
    │   ├── RunLog.md              ← import history (written by engine)
    │   ├── MappingReport.md       ← column→canonical results (written by engine)
    │   ├── registry.csv           ← entity registry (written by engine)
    │   ├── entity_network_metrics.csv
    │   ├── entity_network_analysis_report.txt
    │   └── diagnostics/
    ├── _ontology/                 ← browseable ontology notes (optional)
    ├── _mappings/                 ← editable column mapping overrides
    ├── _rules/
    │   ├── phone.yaml
    │   └── linkedin.yaml
    ├── Company/
    │   ├── _index.md
    │   ├── _teams/
    │   │   ├── IcAlps/views/
    │   │   └── SEALSQ/views/
    │   ├── IcAlps/                ← Company records for IcAlps team
    │   └── SEALSQ/                ← Company records for SEALSQ team
    ├── Contact/
    │   ├── _index.md
    │   ├── _teams/
    │   │   ├── IcAlps/views/
    │   │   └── SEALSQ/views/
    │   ├── IcAlps/
    │   └── SEALSQ/
    ├── Deal/
    │   ├── _index.md
    │   ├── _teams/
    │   │   ├── IcAlps/views/
    │   │   └── SEALSQ/views/
    │   ├── IcAlps/
    │   └── SEALSQ/
    └── Communication/
        ├── _index.md
        ├── _teams/
        │   ├── IcAlps/views/
        │   └── SEALSQ/views/
        ├── IcAlps/
        └── SEALSQ/
```

---

## 4. Recommended Obsidian plugins

| Plugin | ID | Status | Purpose |
|--------|----|--------|---------|
| **Dataview** | `dataview` | ✅ Required | Powers dynamic table views per entity/team |
| **Folder Notes** | `folder-notes` | ✅ Required | Makes `_index.md` the folder note for each entity |
| **Waypoint** | `waypoint` | ⭐ Optional | Auto-generates MOC tables of contents in `_index.md` |
| **Graph Analysis** | `obsidian-graph-analysis` | ⭐ Optional | Visualises entity relationships in the graph view |

Install plugins via **Settings → Community Plugins → Browse** in Obsidian.

---

## 5. Re-running bootstrap

`bootstrap.py` is fully **idempotent** — it is safe to run multiple times:

- It **never overwrites** existing `.md` or `.yaml` files.
- It **never modifies** any `.obsidian/` file other than `app.json`
  (and only if `app.json` is missing).
- Each run **appends** a new section to `crm/_system/BootstrapLog.md`.
- All directories are created with `mkdir -p` semantics.

---

## 6. Troubleshooting

| Symptom | Fix |
|---------|-----|
| `[ERROR  ] Vault root directory does not exist` | Create the target folder or pass the correct `--vault` path |
| `[WARN   ] .obsidian/ directory not found` | Open the vault folder in Obsidian once, then re-run bootstrap |
| `[ERROR  ] crm_ontology.yaml not found` | Pass the correct `--ontology` path |
| `[ERROR  ] crm_ontology.yaml is invalid` | Ensure the file has a top-level `ontology:` key |
| `[WARN   ] Plugin 'dataview' not found` | Install the plugin in Obsidian Community Plugins |
| `[WARN   ] 'rapidfuzz' not found` | `pip install rapidfuzz` to enable fuzzy column matching |
