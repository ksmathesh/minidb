# MiniDB — Lightweight Relational Database Engine

**MiniDB is a Python database engine for studying storage, indexing, SQL execution, and buffer-pool cache pollution.** It connects a small SQL parser and query executor to slotted-page storage, a buffer pool, and a B+ tree primary-key index.

## Features

- Fixed 4 KiB pages with slotted-page record insert, lookup, delete, slot reuse, serialization, and structural validation.
- Per-table redo write-ahead log with checksummed full-page records, startup replay, torn-tail handling, and checkpoints; catalog JSON is replaced atomically.
- Buffer pool with page-table lookup, pin counts, dirty-page writeback, hit/miss metrics, and LRU or Clock replacement.
- In-memory B+ tree with primary-key lookup and linked-leaf range scans.
- Persistent multi-table catalog and one page file per table.
- SQL subset: `CREATE DATABASE`, `SHOW DATABASES`, `USE`, `CREATE TABLE`, `DROP TABLE`, `SHOW TABLES`, `DESCRIBE`, `INSERT`, `SELECT`, `UPDATE`, and `DELETE`; SELECT supports filters, indexed ranges, ordering, limits, and simple aggregates/grouping.
- Schema validation for column names, required insert fields, integer/text values, filter columns, and aggregate types.
- Isolated named databases that users can create from the browser workbench; each database has its own persistent catalog and tables.
- Local browser workbench for multiple databases and tables, schema inspection, SQL results, and live engine state.
- LRU/Clock benchmark suite across four workload configurations and five fixed seeds, with per-iteration and aggregate CSV files.

## Run

Use Python 3.10 or newer. The engine uses only the Python standard library.

```powershell
python -m unittest -v
python demo.py
python cli.py        # interactive SQL shell with database switching
python benchmark_pollution.py
python web_app.py
```

The workbench opens at `http://127.0.0.1:8000`. Use **+ New database** in the header to create a separate database, then run `CREATE TABLE ...` in its SQL editor. Databases persist under `minidb_web_catalog/databases/`. The existing `default` database continues to use the original catalog. If port 8000 is unavailable, set a different port first, for example `$env:MINIDB_PORT = "8765"`. Keep that PowerShell window open while using the UI, then stop the server with `Ctrl+C`.

## Database workflow

The SQL shell and browser editor support `CREATE DATABASE`, `SHOW DATABASES`, `USE`, `BEGIN`, `COMMIT`, and `ROLLBACK`. Each database has an independent table catalog. Transactions are limited to one database; an error inside an active transaction automatically rolls it back. The page WAL replays durable page images after restart, but transaction commit/rollback is not crash-atomic: a crash during a multi-page transaction can recover a partial statement. In the shell, the prompt shows the selected database.

```sql
CREATE DATABASE notes;
SHOW DATABASES;
USE notes;
CREATE TABLE pages (id INT PRIMARY KEY, title TEXT);
INSERT INTO pages (id, title) VALUES (1, 'Getting started');
SELECT * FROM pages;
BEGIN;
UPDATE pages SET title = 'Draft' WHERE id = 1;
ROLLBACK;
```

The browser database dropdown provides the same isolation, and SQL `USE` switches it to the selected database. You can submit several statements together by separating them with semicolons; they run in order. Use `BEGIN; ... COMMIT;` to make a batch rollback as a unit if a statement fails.

## Example SQL

```sql
CREATE TABLE users (id INT PRIMARY KEY, name TEXT, role TEXT);
INSERT INTO users (id, name, role) VALUES (1, 'Ada', 'admin');
SELECT name FROM users WHERE id = 1;
SELECT id FROM users WHERE id >= 10 AND id < 20;
SELECT role, COUNT(*) AS total FROM users GROUP BY role ORDER BY total DESC;
```

`DatabaseEngine` persists table schemas and data. The B+ tree is rebuilt from table pages when the engine opens. Inserts must provide every declared column because MiniDB does not implement NULL values or defaults.

## Cache experiment

`python benchmark_pollution.py` runs four configurations—baseline, larger hot set, longer scan, and larger pool—across five deterministic seeds (42–46), with 20 iterations and ten hot requests per iteration. Both eviction policies receive the same trace for each seed. The command writes:

- `cache_pollution_timeline.csv`: per-iteration hit data for every scenario, policy, and seed.
- `cache_pollution_summary.csv`: mean and sample standard deviation across seeds for each scenario and policy.
- `cache_pollution_hit_rate.svg`: average hot-query hit-rate chart with one-standard-deviation error bars.

Compare results within each workload. No policy is universally better; results depend on the working-set and scan pattern. Runtime is collected inside each run but omitted from the summary because these in-memory experiments are too short for stable timing comparisons.

## Scope and limitations

MiniDB is single-process and single-threaded. It has page-level redo recovery, but not transaction-level crash atomicity, WAL undo, concurrent transaction isolation, or persisted indexes. It does not implement joins, NULL/default values, or broad SQL compatibility. The SQL parser intentionally handles a small grammar. The web workbench uses the same multi-table catalog as the command-line engine, but remains a local educational client rather than a production server.
