# Wiki Space Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade lark-autocontext from folder-based to Feishu Wiki Space-based, adding bidirectional sync, FTS5 query engine, and OKF↔Feishu docx conversion.

**Architecture:** Three new modules (`wiki_connector.py`, `dual_storage.py`, `query_engine.py`) layered on existing scripts. Wiki connector wraps all Feishu Wiki Space read/write via lark-cli. Dual storage coordinates bidirectional sync with conflict detection. Query engine replaces substring matching with SQLite FTS5 + progressive RAG.

**Tech Stack:** Python 3.10+, lark-cli, SQLite3 (stdlib, FTS5 extension), pyyaml, pytest

**Spec:** `docs/superpowers/specs/2026-07-21-wiki-space-upgrade-design.md`

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `scripts/wiki_connector.py` | Feishu Wiki Space read/write via lark-cli, OKF↔Feishu conversion |
| `scripts/dual_storage.py` | Bidirectional sync coordinator, sync_state.json, conflict resolution |
| `scripts/query_engine.py` | SQLite FTS5 index + 3-stage progressive query (recall→filter→deep read) |
| `scripts/migrate_to_wiki.py` | One-time migration from folder config to wiki config |
| `tests/unit/test_wiki_connector.py` | Unit tests for connector + conversion |
| `tests/unit/test_dual_storage.py` | Unit tests for sync + conflict |
| `tests/unit/test_query_engine.py` | Unit tests for FTS5 + query |
| `tests/unit/test_okf_feishu_convert.py` | OKF↔Feishu conversion tests |
| `tests/unit/test_scanner_wiki.py` | Scanner wiki integration tests |
| `tests/unit/test_okf_writer_index_hook.py` | Index update hook tests |
| `tests/unit/test_auto_sync_wiki.py` | Auto sync wiki mode tests |
| `tests/unit/test_onboarding_wiki.py` | Onboarding wiki checks tests |
| `tests/unit/test_migrate_to_wiki.py` | Migration tool tests |
| `tests/integration/test_sync_flow.py` | End-to-end sync flow |
| `tests/integration/test_query_flow.py` | End-to-end query flow |

### Modified Files

| File | Change |
|------|--------|
| `scripts/scanner.py` | Add `use_wiki` parameter, use WikiConnector |
| `scripts/okf_writer.py` | Add `generate_index_pages()`, call `query_engine.update_index()` after write |
| `scripts/auto_sync.py` | Add wiki mode to `list-only` |
| `scripts/init_bundle.py` | Create `.index/` and `.conflicts/` dirs |
| `scripts/onboarding.py` | Add FTS5 + sync_state + wiki config checks |
| `scripts/config.json.example` | Add wiki fields |
| `SKILL.md` | Update Workflows A-D for wiki mode |
| `.gitignore` | Ignore `.index/search.db` |

---

## Phase 1: Foundation — Wiki Connector (Tasks 1-4)

### Task 1: WikiConnector skeleton with data structures

**Files:** Create `scripts/wiki_connector.py`, `tests/unit/test_wiki_connector.py`

- [ ] **Step 1: Write failing test** for `WikiConnector.__init__`, `DocInfo`, `DocMeta` dataclasses
- [ ] **Step 2: Run test** — expect `ModuleNotFoundError`
- [ ] **Step 3: Implement** `WikiConnector` class with `__init__`, `_run_lark` helper (429 retry, timeout), `DocInfo`/`DocMeta` dataclasses
- [ ] **Step 4: Run test** — expect PASS
- [ ] **Step 5: Commit** `feat(wiki_connector): add skeleton with data structures`

### Task 2: Read operations — list_raw_docs, fetch_doc_content, fetch_doc_meta

**Files:** Modify `scripts/wiki_connector.py`, `tests/unit/test_wiki_connector.py`

- [ ] **Step 1: Write failing tests** for `list_raw_docs` (with/without since filter), `fetch_doc_content`, `fetch_doc_meta`, `list_wiki_subtree`, `list_agent_docs`, 429 retry
- [ ] **Step 2: Run tests** — expect `AttributeError`
- [ ] **Step 3: Implement** read operations using `_run_lark` with `wiki +list-nodes`, `wiki +fetch-node`, `wiki +get-node` commands. Call `scanner.clean_feishu_content()` in `fetch_doc_content`.
- [ ] **Step 4: Run tests** — expect PASS (8 tests)
- [ ] **Step 5: Commit** `feat(wiki_connector): implement read operations`

### Task 3: Write operations — create_doc, update_doc, upload_attachment

**Files:** Modify `scripts/wiki_connector.py`, `tests/unit/test_wiki_connector.py`

- [ ] **Step 1: Write failing tests** for `create_doc` (returns node_token), `update_doc`, `upload_attachment`, `delete_doc`, `move_doc`, `check_doc_changed`
- [ ] **Step 2: Run tests** — expect `AttributeError`
- [ ] **Step 3: Implement** write operations using temp files for content upload, `wiki +create-node`, `wiki +update-node`, `wiki +delete-node`, `wiki +move-node` commands
- [ ] **Step 4: Run tests** — expect PASS (14 tests total)
- [ ] **Step 5: Commit** `feat(wiki_connector): implement write operations`

### Task 4: OKF ↔ Feishu docx conversion

**Files:** Modify `scripts/wiki_connector.py`, Create `tests/unit/test_okf_feishu_convert.py`

- [ ] **Step 1: Write failing tests** for `okf_to_feishu_content` (strips frontmatter, adds metadata header), `feishu_to_okf_body` (strips header, preserves frontmatter), `generate_metadata_header`, `strip_metadata_header`
- [ ] **Step 2: Run tests** — expect `ImportError`
- [ ] **Step 3: Implement** conversion functions: `_parse_frontmatter`, `generate_metadata_header` (emoji format), `strip_metadata_header`, `okf_to_feishu_content`, `feishu_to_okf_body` (delegates to `scanner.clean_feishu_content`)
- [ ] **Step 4: Run tests** — expect PASS (9 tests)
- [ ] **Step 5: Commit** `feat(wiki_connector): OKF ↔ Feishu docx conversion`

---

## Phase 2: Dual Storage — Bidirectional Sync (Tasks 5-7)

### Task 5: DualStorage class with sync_state management

**Files:** Create `scripts/dual_storage.py`, `tests/unit/test_dual_storage.py`

- [ ] **Step 1: Write failing tests** for `load_state` (empty/existing/corrupted), `save_state` (atomic write), `SyncDirection` enum
- [ ] **Step 2: Run tests** — expect `ModuleNotFoundError`
- [ ] **Step 3: Implement** `SyncDirection` enum, `SyncState` dataclass, `DualStorage` class with `load_state`/`save_state` (atomic tmp+replace), `_compute_hash` (SHA256), corruption recovery
- [ ] **Step 4: Run tests** — expect PASS (5 tests)
- [ ] **Step 5: Commit** `feat(dual_storage): add sync_state management`

### Task 6: sync_to_feishu — local → Feishu push

**Files:** Modify `scripts/dual_storage.py`, `tests/unit/test_dual_storage.py`

- [ ] **Step 1: Write failing tests** for new doc creation, existing doc update, failure keeps `local_newer`
- [ ] **Step 2: Run tests** — expect `AttributeError`
- [ ] **Step 3: Implement** `SyncResult` dataclass, `sync_to_feishu` method: parse frontmatter, convert OKF→Feishu, find existing by path, create or update, update sync_state. On failure, return `SyncResult(success=False)` without updating state.
- [ ] **Step 4: Run tests** — expect PASS (8 tests total)
- [ ] **Step 5: Commit** `feat(dual_storage): implement sync_to_feishu`

### Task 7: detect_feishu_edits and pull_from_feishu

**Files:** Modify `scripts/dual_storage.py`, `tests/unit/test_dual_storage.py`

- [ ] **Step 1: Write failing tests** for `detect_feishu_edits` (finds changed docs), `pull_from_feishu` (no conflict = safe overwrite, conflict = Feishu wins + backup)
- [ ] **Step 2: Run tests** — expect `AttributeError`
- [ ] **Step 3: Implement** `SyncItem` dataclass, `detect_feishu_edits` (compare modified_time), `pull_from_feishu` (fetch→convert→conflict check→write), `_backup_conflict` (backup to `.conflicts/`, append to `log.md`)
- [ ] **Step 4: Run tests** — expect PASS (11 tests total)
- [ ] **Step 5: Commit** `feat(dual_storage): implement detect and pull with conflict resolution`

---

## Phase 3: Query Engine — FTS5 + Progressive RAG (Tasks 8-11)

### Task 8: QueryEngine class with FTS5 schema

**Files:** Create `scripts/query_engine.py`, `tests/unit/test_query_engine.py`

- [ ] **Step 1: Write failing tests** for `ensure_index` (creates dir + db), FTS5 table existence
- [ ] **Step 2: Run tests** — expect `ModuleNotFoundError`
- [ ] **Step 3: Implement** `SearchFilters`/`DocMatch`/`SearchResult` dataclasses, `QueryEngine` class with `ensure_index` (creates `documents` table + `documents_fts` virtual table with `unicode61` tokenizer + sync triggers)
- [ ] **Step 4: Run tests** — expect PASS (2 tests)
- [ ] **Step 5: Commit** `feat(query_engine): add FTS5 schema`

### Task 9: Index build and update operations

**Files:** Modify `scripts/query_engine.py`, `tests/unit/test_query_engine.py`

- [ ] **Step 1: Write failing tests** for `update_index` (single doc), `remove_from_index`, `rebuild_index` (full scan, excludes index.md/log.md), hash-skip if unchanged
- [ ] **Step 2: Run tests** — expect `AttributeError`
- [ ] **Step 3: Implement** `_parse_okf`, `_compute_hash`, `_extract_body_text` (strip markdown formatting), `update_index` (INSERT OR REPLACE with hash check), `remove_from_index`, `rebuild_index` (walk bundle, skip hidden dirs + index.md + log.md)
- [ ] **Step 4: Run tests** — expect PASS (6 tests total)
- [ ] **Step 5: Commit** `feat(query_engine): implement index build and update`

### Task 10: Search with FTS5 recall and structured filtering

**Files:** Modify `scripts/query_engine.py`, `tests/unit/test_query_engine.py`

- [ ] **Step 1: Write failing tests** for basic keyword search, Chinese keyword, project filter, type filter, no results, snippet return, deep read with full content, no deep read
- [ ] **Step 2: Run tests** — expect `AttributeError`
- [ ] **Step 3: Implement** `_calculate_score` (FTS×0.6 + time_decay×0.2 + type_weight×0.2), `search` (FTS5 MATCH + bm25 + snippet, structured filtering, scoring, sorting), `_deep_read` (read full content, 8000 char cap, section markers)
- [ ] **Step 4: Run tests** — expect PASS (14 tests total)
- [ ] **Step 5: Commit** `feat(query_engine): implement 3-stage progressive search`

### Task 11: CLI interface for query_engine

**Files:** Modify `scripts/query_engine.py`, `tests/unit/test_query_engine.py`

- [ ] **Step 1: Write failing tests** for `search`, `rebuild`, `status` CLI commands (JSON output)
- [ ] **Step 2: Run tests** — expect `ImportError`
- [ ] **Step 3: Implement** `main()` with argparse: `search` (query + filters + deep read), `rebuild`, `status`. JSON output for Agent consumption.
- [ ] **Step 4: Run tests** — expect PASS (17 tests total)
- [ ] **Step 5: Commit** `feat(query_engine): add CLI interface`

---

## Phase 4: Integration — Wire Up Existing Modules (Tasks 12-15)

### Task 12: Update scanner.py for wiki mode

**Files:** Modify `scripts/scanner.py`, Create `tests/unit/test_scanner_wiki.py`

- [ ] **Step 1: Write failing tests** for `scan_single_doc(use_wiki=True)`, `scan_batch(use_wiki=True)` using mock WikiConnector
- [ ] **Step 2: Run tests** — expect `TypeError`
- [ ] **Step 3: Implement** Add `use_wiki` and `wiki_connector` params to `scan_single_doc` and `scan_batch`. Add `_get_wiki_connector()` factory. Wiki mode uses `fetch_doc_content` + `fetch_doc_meta`. Legacy folder mode preserved as fallback.
- [ ] **Step 4: Run tests** — expect PASS (existing cleaning tests still pass)
- [ ] **Step 5: Commit** `feat(scanner): add wiki connector integration`

### Task 13: Update okf_writer.py with index hook and generate_index_pages

**Files:** Modify `scripts/okf_writer.py`, Create `tests/unit/test_okf_writer_index_hook.py`

- [ ] **Step 1: Write failing tests** for index update hook (mock QueryEngine, verify `update_index` called after write), `generate_index_pages` (creates navigation doc)
- [ ] **Step 2: Run tests** — expect `ImportError`
- [ ] **Step 3: Implement** Import `QueryEngine`, call `engine.update_index(file_path)` at end of `write_okf_document` (best-effort try/except). Add `generate_index_pages()` — scans bundle, groups by project/type, generates navigation Markdown with people/concepts sections.
- [ ] **Step 4: Run tests** — expect PASS
- [ ] **Step 5: Commit** `feat(okf_writer): add index hook and generate_index_pages`

### Task 14: Update auto_sync.py for wiki mode

**Files:** Modify `scripts/auto_sync.py`, Create `tests/unit/test_auto_sync_wiki.py`

- [ ] **Step 1: Write failing test** for `list-only` in wiki mode (uses WikiConnector.list_raw_docs with since filter)
- [ ] **Step 2: Run test** — expect FAIL
- [ ] **Step 3: Implement** Import `WikiConnector`. In `cmd_list_only`, detect wiki config. If wiki mode: use `conn.list_raw_docs(since=last_scan_at)`, write pending_changes.json. Folder mode preserved as fallback.
- [ ] **Step 4: Run test** — expect PASS
- [ ] **Step 5: Commit** `feat(auto_sync): add wiki mode for list-only`

### Task 15: Update onboarding.py, init_bundle.py, config.json.example, .gitignore

**Files:** Modify `scripts/onboarding.py`, `scripts/init_bundle.py`, `scripts/config.json.example`, `.gitignore`, Create `tests/unit/test_onboarding_wiki.py`

- [ ] **Step 1: Write failing tests** for wiki config check, FTS5 availability check, sync_state check
- [ ] **Step 2: Run tests** — expect FAIL
- [ ] **Step 3: Implement** onboarding: add checks for wiki config, FTS5, sync_state, .index dir. init_bundle: add `.index/` and `.conflicts/` dirs. config.json.example: add wiki fields. .gitignore: add search.db exclusion.
- [ ] **Step 4: Run tests** — expect PASS
- [ ] **Step 5: Commit** `feat(onboarding): add wiki config, FTS5, sync_state checks`

---

## Phase 5: Migration Tool & SKILL.md (Tasks 16-17)

### Task 16: Create migrate_to_wiki.py

**Files:** Create `scripts/migrate_to_wiki.py`, `tests/unit/test_migrate_to_wiki.py`

- [ ] **Step 1: Write failing tests** for `migrate_config` (adds wiki fields, backs up old config, preserves existing fields)
- [ ] **Step 2: Run tests** — expect `ModuleNotFoundError`
- [ ] **Step 3: Implement** `migrate_config()`: read existing, backup, add wiki fields, add scan_sources, write new config. CLI with `--space-id`, `--raw-node`, `--agent-node`.
- [ ] **Step 4: Run tests** — expect PASS (3 tests)
- [ ] **Step 5: Commit** `feat(migrate): add folder-to-wiki migration tool`

### Task 17: Update SKILL.md with wiki workflows

**Files:** Modify `SKILL.md`

- [ ] **Step 1: Read current SKILL.md** to understand existing structure
- [ ] **Step 2: Update workflow sections** surgically:
  - Pre-flight: add wiki config + FTS5 + sync_state checks
  - Workflow A: `wiki_connector.fetch_doc_content` → `dual_storage.sync_to_feishu` → `query_engine.update_index`
  - Workflow B: `wiki_connector.list_raw_docs`
  - Workflow C: `dual_storage.detect_feishu_edits` + `query_engine.search`
  - Workflow D: wiki `list-only` + `detect_feishu_edits` in finalize
  - New triggers: `问一下知识库`, `查一下业务`, `同步飞书知识库`
- [ ] **Step 3: Verify SKILL.md** is valid
- [ ] **Step 4: Commit** `docs(skill): update workflows for wiki space mode`

---

## Phase 6: Integration Tests (Tasks 18-19)

### Task 18: End-to-end sync flow test

**Files:** Create `tests/integration/test_sync_flow.py`

- [ ] **Step 1: Write integration test** for full flow: write OKF → index → search finds it; sync to Feishu → detect edit → pull back → local updated
- [ ] **Step 2: Run test** — expect PASS
- [ ] **Step 3: Commit** `test(integration): end-to-end sync flow`

### Task 19: End-to-end query flow test

**Files:** Create `tests/integration/test_query_flow.py`

- [ ] **Step 1: Write integration test** for multiple docs → index → keyword search, type filter, deep read context assembly, no-deep-read mode
- [ ] **Step 2: Run test** — expect PASS
- [ ] **Step 3: Commit** `test(integration): end-to-end query flow`

---

## Phase 7: Final Polish (Task 20)

### Task 20: Full test suite run and fixes

- [ ] **Step 1: Run all tests** `python -m pytest tests/ -v --tb=short`
- [ ] **Step 2: Fix any failures** (import paths, mock setup, fixtures)
- [ ] **Step 3: Run tests again** — all PASS
- [ ] **Step 4: Commit fixes** `fix: resolve test failures from integration`
- [ ] **Step 5: Final commit** `chore: wiki space upgrade complete`

---

## Self-Review Notes

**Spec coverage check:**
- wiki_connector.py (Tasks 1-4): read/write/conversion — covers spec §3.1
- dual_storage.py (Tasks 5-7): sync_state, push, pull, conflict — covers spec §3.2
- query_engine.py (Tasks 8-11): FTS5, indexing, search, CLI — covers spec §3.3
- scanner.py update (Task 12) — covers spec §2.3
- okf_writer.py update (Task 13) — covers spec §4.3
- auto_sync.py update (Task 14) — covers spec §4.2 Workflow D
- onboarding/init_bundle/config (Task 15) — covers spec §5.4, §5.5
- migrate_to_wiki.py (Task 16) — covers spec §5.3
- SKILL.md update (Task 17) — covers spec §4.2, §4.4
- Integration tests (Tasks 18-19) — covers spec §5.2
- Error handling matrix (distributed across tasks) — covers spec §5.1

**Type consistency check:**
- DocInfo fields: node_token, title, obj_type, modified_time, url, has_children — consistent across Tasks 1-4
- SyncDirection enum: IN_SYNC, LOCAL_NEWER, FEISHU_NEWER, CONFLICT — consistent across Tasks 5-7
- SyncResult fields: success, feishu_node_token, feishu_url, error, action — consistent across Tasks 6-7
- SearchFilters fields: project, doc_type, tags, people, date_from, date_to — consistent across Tasks 8-11
- DocMatch fields: local_path, title, doc_type, score, snippet, full_content, related_docs, feishu_url — consistent across Tasks 8-11
