# AutoNormalizer Plus: ER diagram, 4NF, what-if, AI layer

Drop-in modules for AutoNormalizer. Pure-Python engine + one FastAPI router + a demo page. 49 tests.

**Idea:** messy table in, verified normalized design out. AI proposes and explains; the data and the formal engine verify.

## Pages (Back / Next wizard, no long scrolling)
1 Data, 2 Dependencies and key, 3 Result and proof, 4 ER diagram + SQL + ZIP download, 5 Explanation (what is wrong / how fixed / viva), 6 4NF, 7 What-if.
Click a numbered step at the top to jump to any step already reached.

## Run the demo
Windows shortcut: double-click `run_demo.bat` (creates `.venv`, installs packages, opens the browser). Or manually:
```bash
pip install -r requirements.txt
python -m pytest backend -q                                # 49 tests
uvicorn backend.api.demo_app:app --reload                  # open http://127.0.0.1:8000
```
Optional real AI (key stays on the server, never in the browser):
```bash
set ANTHROPIC_API_KEY=your_key          # Windows  (Mac/Linux: export ...)
set LLM_MODEL=claude-sonnet-5-5         # optional, change if your account uses another model
```
Without a key every AI feature falls back to templates/heuristics and says so ("template (no AI key)").

## Sample files (in `sample_data/`, upload with "Choose File")
| File | Shows |
|---|---|
| `college_enrollments.csv` | 40 rows, one flat sheet. Result: Students, Courses, Faculties, Enrollment with foreign keys, lossless proof |
| `online_orders_with_typo.csv` | 38 rows with ONE deliberate typo (`vikram singh`). `CustomerID -> CustomerName` shows as *approximate* with the conflicting rows. Tick it and the tool reports "Not lossless on your data" instead of hiding the problem |
| `course_teacher_book.csv` | Already BCNF but not 4NF. The tool finds `Course ->> Teacher \| Book` and splits it, lossless |

## What was added
| Feature | Module | What it does |
|---|---|---|
| ER diagram | `engine/er_diagram.py` | entities with PK / FK / unique, relationships with cardinality, Mermaid text, DDL with foreign keys |
| MVD + 4NF | `engine/mvd_engine.py` | check/discover MVDs in data (cross-product test), 4NF violation detection, 4NF decomposition |
| What-if | `engine/whatif.py` | add/remove FD or attribute on a copy; before/after keys, normal form, violations, decompositions |
| Data evidence | `engine/data_tools.py` | FD check with counterexamples, FD discovery (levelwise, minimal), reconstruction proof (join the new tables back and compare with the original rows) |
| SQL export | `engine/export.py`, `engine/er_diagram.py` | ZIP with one CSV per table + `schema.sql` (CREATE, INSERT, foreign keys) in PostgreSQL, MySQL or Oracle dialect. Refuses to export if your data contradicts a primary key |
| AI layer | `ai/` | suggest FDs (verified against data), name tables (validated), explain findings (only engine-found violations allowed) |

## Plug into your existing FastAPI app
```python
from backend.api.plus_routes import router as plus_router
app.include_router(plus_router)
```
| Endpoint | Input | Output |
|---|---|---|
| `POST /plus/profile` | `csv_text` | columns, types, FD candidates with evidence (supported / approximate / contradicted / trivial) |
| `POST /plus/analyze` | FDs + `csv_text` (or attributes) | normal form, keys, violations, decomposition, table names, ER (entities, relationships, Mermaid), DDL, reconstruction proof, explanation, MVD candidates |
| `POST /plus/4nf` | MVDs (+ FDs, data) | 4NF violations, steps, tables, ER, reconstruction proof |
| `POST /plus/whatif` | attributes, FDs, `changes` | before, after, diff with plain summary lines |
| `POST /plus/export` | `csv_text`, `relations` + `names` (from `/analyze`), `dialect` | ZIP download, or 409 with the conflicting rows |
| `POST /plus/er` | relations + FDs | ER JSON, Mermaid, DDL |

Change format for what-if: `{"op": "add_fd"|"remove_fd", "lhs": [...], "rhs": [...]}` or `{"op": "add_attribute"|"remove_attribute", "name": "..."}`.
In React: render `mermaid` with the `mermaid` npm package, or build a React Flow graph from `er.entities` / `er.relationships`.

## Design rules (say these in the viva)
- AI never decides correctness. Every AI-proposed FD is checked against the rows; every AI explanation is restricted to violations the engine found; AI table names must pass validation.
- A dependency found in data is a hypothesis, not a business rule. The user accepts or rejects each one.
- Lossless join is demonstrated on the real data, not only proved on paper.
- Removing an FD can make a schema worse (the key grows); the what-if shows this.

## What was verified by running it
- **PostgreSQL 16:** `schema.sql` loaded, the 4 tables joined back gave all 40 original rows with 0 missing and 0 extra rows against the original CSV, and foreign keys rejected a bad row.
- **MySQL (MariaDB 10.11):** same result (40 / 40, 0 missing, 0 extra), foreign keys enforced.
- **Oracle: NOT tested** (no Oracle server available). The script uses `VARCHAR2`, `NUMBER`, unquoted identifiers, one INSERT per row and `COMMIT;`. Try it in your lab and report any error.

## Honest limits
- Real LLM calls were not run (no key in the build environment). Request building and response parsing are tested with a mocked HTTP transport.
- The demo page was not opened in a browser by me. Its JavaScript passes a syntax check and the generated ER text parses with the real Mermaid library.
- MVD support works on declared or data-discovered MVDs (plus complements and FDs). It does not compute a full dependency basis.
- FD discovery is exact/approximate, left side up to 2 columns, capped at 20,000 rows and 30 columns. Not a replacement for TANE/HyFD at scale.
- Offline table names are rough when columns are abbreviations (`sid` becomes `Sids`); the AI names are better.
- Export covers the 3NF/BCNF result shown on the page, not the 4NF result.
- Data cleaning is out of scope: conflicting rows are shown as evidence, never silently changed.


## Product status and safety notes
This is a standalone AutoNormalizer Plus prototype based on the uploaded code. It includes CSV profiling, data-backed FD suggestions, 3NF/BCNF and 4NF workflows, ER/DDL generation, what-if analysis, reconstruction checks, and optional server-side AI integration. The uploaded backend test suite passes (49 tests).

The included UI has been visually polished, but this package does **not** yet implement production user authentication, multi-user workspaces, live database connection management, or production-grade deployment/security. Do not upload confidential datasets into an untrusted environment. Review generated SQL before executing it. AI requires a configured provider key; without one, template/heuristic explanations are used.

The original uploaded project is not modified. This is a separate build folder/package.

## Full-window workspace update

The browser UI now uses a full-window workspace layout instead of a constrained wizard card. Navigation tabs remain available so you can move between Data Workspace, Dependencies, Normalization, ER & SQL, AI Insights, 4NF Analysis, and What-if Lab. The data workspace adds a client-side CSV preview and quick counts for rows, columns, empty cells, and duplicate rows. These preview counts are informational; the backend remains the source of truth for dependency analysis and normalization proofs.

To use it on Windows, run `run_demo.bat` from this folder and open `http://127.0.0.1:8000`.
