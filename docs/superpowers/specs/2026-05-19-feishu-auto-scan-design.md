# Feishu Auto Scan Design

## Context

Context Wizard currently depends on the user manually providing a Feishu document, sheet, image, or text snippet. That workflow is accurate and easy to control, but it does not scale when the user's business context is spread across many Feishu documents, spreadsheets, wiki pages, and Base tables.

The upgrade adds an automatic discovery layer that can scan Feishu content the user can access, prioritize likely business-context documents, and feed high-confidence candidates into the existing extraction and storage flow.

## Goals

- Discover useful Feishu documents without requiring the user to paste links manually.
- Support broad scanning across accessible Feishu cloud documents.
- Avoid being overwhelmed by large document volumes.
- Automatically ingest high-confidence candidates.
- Keep medium- and low-confidence candidates reviewable instead of silently dropping them.
- Preserve the current Context Wizard storage and retrieval model.
- Make scanning resumable, rate-limit aware, and safe to rerun.

## Non-Goals

- Build a full Feishu document crawler in the first version.
- Read and summarize every accessible document immediately.
- Replace the existing manual save flow.
- Build a complex web UI or Base-based review console in the first version.
- Guarantee perfect historical coverage in a single scan run.

## Recommended Approach

Use `lark-cli drive +search` as the discovery source. It already supports document type filters, time windows, sorting, pagination, and empty-query browsing. This keeps the first version lightweight and aligned with the existing Python-plus-CLI architecture.

The first version should combine search-driven discovery with a lightweight local state file. It should not introduce SQLite or a Feishu Base control console until the state and review workflows need more querying or collaboration features.

## Architecture

Add an automatic discovery layer without changing the current extraction, preview, write, and search flow.

Planned modules:

- `scan_drive.py`: runs Feishu Drive search in daily and weekly modes.
- `score_candidates.py`: scores discovered files using metadata and lightweight heuristics.
- `ingest_candidates.py`: reads high-confidence candidates, calls the existing extraction and storage chain, and sends uncertain candidates to review.
- `scan_state.jsonl`: records candidate state, fingerprints, attempts, failures, and queue decisions.

The central rule is to decouple discovery from full-content ingestion. Search can be broad, but reading full document content and running extraction must stay budgeted.

## Scan Lanes

The scanner uses three lanes so recent work and old documents do not compete in a single queue.

### Freshness Lane

The daily scan handles recent activity.

- Runs once per day.
- Searches documents created, edited, or opened in the last 1-2 days.
- Covers `docx`, `doc`, `sheet`, `bitable`, `wiki`, and `slides`.
- Defaults to discovering up to 100 candidates per run.
- Defaults to full-content ingestion for up to 20 high-confidence candidates per run.

This lane protects timeliness.

### Backfill Lane

The weekly scan handles historical coverage.

- Runs once per week.
- Searches longer historical windows, starting with the last 6-12 months and then moving backward.
- Uses a persistent `backfill_cursor` with the active time window and pagination token.
- Defaults to discovering up to 200 older candidates per weekly run.
- Defaults to full-content ingestion for up to 50 high-confidence historical candidates per weekly run.

This lane prevents older but valuable documents from being permanently starved by new activity.

### Review Lane

The review lane stores candidates that should not be auto-ingested.

Examples:

- Medium-confidence documents.
- Very large documents or spreadsheets.
- Documents with incomplete extraction results.
- Documents that look important but failed processing.
- Documents where the project or entity cannot be determined confidently.

Review candidates remain in state and can be approved, skipped, or rescanned later.

## Candidate Scoring

Each discovered candidate receives a score before full content is read.

Positive signals:

- Recently edited or created.
- Title or path matches known project, client, product, or partner terms.
- Document type is likely useful: `docx`, `doc`, `wiki`, `sheet`, or `bitable`.
- Title contains terms such as `会议纪要`, `复盘`, `需求`, `方案`, `决策`, `合同`, `数据分析`, `竞品`, or `OKR`.
- Search metadata indicates user interaction, such as recently opened or edited by the user.

Negative or caution signals:

- Already processed and unchanged.
- Looks like a template, draft, scratch note, or personal temporary file.
- Spreadsheet or Base appears too large for a safe first pass.
- Missing title, token, URL, or type metadata.
- Previous processing failed repeatedly.

Scoring produces one of three actions:

- `auto_ingest`: high-confidence candidate; read full content and write to Context Wizard.
- `needs_review`: uncertain or risky candidate; keep it for user review.
- `defer` or `skipped`: low-value, unchanged, or user-skipped candidate.

## Direct Semi-Automatic Ingestion

The default mode is not dry-run.

High-confidence candidates should be automatically ingested during scheduled runs. The trial nature of this project makes direct execution acceptable, and the existing source links plus state tracking keep the process inspectable.

The auto-ingest threshold should still be conservative. A candidate may be auto-ingested only when:

- Metadata score is high enough.
- Full-content extraction succeeds.
- Extracted fields include `project_name`, `entity_name`, `core_conclusion`, `source_link`, and `doc_token` or an equivalent stable identifier.
- Deduplication finds no unchanged existing record.
- If the token exists with a newer fingerprint, the system updates or supersedes the prior record instead of blindly duplicating.

If any of these checks fail, the candidate moves to `needs_review` or `failed`.

## State Model

Use a local JSONL state file in the first version, preferably `.context_wizard/scan_state.jsonl` so generated state does not live beside source scripts.

Each record should include:

- `id`: stable internal candidate ID.
- `token`: document, sheet, Base, wiki, or slide token.
- `url`: source URL.
- `doc_type`: Feishu document type.
- `title`: discovered title.
- `owner` or `creator`: when available.
- `created_at`, `updated_at`, and `opened_at`: when available.
- `fingerprint`: token plus type plus update timestamp or equivalent version signal.
- `score`: latest candidate score.
- `lane`: `freshness`, `backfill`, or `review`.
- `status`: `discovered`, `queued`, `auto_ingested`, `needs_review`, `skipped`, or `failed`.
- `reason`: short explanation for the state transition.
- `attempt_count`: processing attempts.
- `last_error`: final user-safe error message.
- `last_seen_at`: when the scanner last saw this candidate.

Use append-friendly JSONL for recoverability. A helper can compact it later by keeping only the latest state per candidate.

## Recovery Rules

- If the same fingerprint is already `auto_ingested`, skip it.
- If the token is known but the fingerprint changed, rescore it.
- If a candidate fails three times, move it to `needs_review`.
- If Feishu returns rate-limit errors, stop the current run, save the cursor, and resume next time.
- If the user marks a candidate `skipped`, do not process it again unless its fingerprint changes.
- If a run crashes, the next run should resume from the latest saved cursor and state records.

## Scheduling

The first implementation should support commands that can be wired to Codex automations, cron, or a local scheduler.

Suggested commands:

```bash
python scripts/scan_drive.py --mode daily
python scripts/scan_drive.py --mode weekly
python scripts/ingest_candidates.py --lane freshness
python scripts/ingest_candidates.py --lane backfill
```

Suggested schedule:

- Daily light scan once per day.
- Weekly deep scan once per week.
- Ingestion can run as part of each scan after scoring.

Each run should print a concise report:

```text
本次扫描:
- 发现候选: 84
- 自动入库: 12
- 待确认: 18
- 跳过/未变化: 49
- 失败: 5
- 历史回填进度: 2025-11-01..2025-12-01 page 3
```

## User Commands

Add lightweight command handling to the skill instructions and scripts:

- `context-wizard scan status`: show recent scan summary and backlog counts.
- `context-wizard review candidates`: show candidates waiting for approval.
- `context-wizard approve <id...>`: approve selected candidates for ingestion.
- `context-wizard skip <id...>`: mark candidates as skipped.
- `context-wizard rescan <token-or-url>`: force rediscovery and rescoring for one source.

These commands can start as script entry points and later be folded into the skill's natural-language workflow.

## Integration With Existing Flow

Auto-ingestion should reuse existing pieces where possible:

- Use `extract_data.py` for document and spreadsheet content extraction.
- Use `get_or_create_table.py` to resolve the target project table.
- Use `write_context.py` for storage.
- Use the existing deduplication intent from `SKILL.md`, but implement it in a reusable helper before auto-ingestion depends on it heavily.

The manual save flow remains available. Automatic scanning is an additional input source, not a replacement.

## Error Handling

Errors should be recorded in state and summarized in reports. Raw tracebacks should not be shown to the user.

Common cases:

- Authentication expired: report that `lark-cli auth login --recommend --no-wait` is needed.
- Missing `config.json`: report that `python scripts/init_base.py` is needed.
- Permission denied on a document: mark `failed` or `needs_review` with a friendly reason.
- Oversized document or spreadsheet: mark `needs_review`.
- Rate limit: save cursor and stop the run.
- Extraction ambiguity: mark `needs_review`.

## Testing

First implementation should include focused smoke tests around pure logic:

- Candidate scoring decisions.
- Fingerprint comparison.
- State append and latest-state compaction.
- Backfill cursor advancement.
- Candidate action routing.

CLI-dependent pieces can be tested with captured sample JSON from `lark-cli drive +search`.

Manual verification:

```bash
python3 -m compileall -q scripts
python3 scripts/onboarding.py
python scripts/scan_drive.py --mode daily --limit 5
python scripts/ingest_candidates.py --limit 2
```

## Risks

- Feishu search results may not expose every metadata field needed for high-quality scoring.
- Empty-query global search behavior may vary by tenant or permission model.
- Some historical documents may require multiple weekly backfill cycles to surface.
- Spreadsheet and Base ingestion may need separate size and extraction rules.
- Automatic ingestion can create noisy records if extraction confidence is too permissive.

The main mitigation is conservative auto-ingest thresholds plus a persistent review queue.

## Phasing

### Phase 1

Implement search-driven discovery, scoring, JSONL state, daily/weekly modes, and direct high-confidence ingestion.

### Phase 2

Add review commands for listing, approving, skipping, and rescanning candidates.

### Phase 3

Add better observability, state compaction, and optional migration from JSONL to SQLite or a Feishu Base review console.

### Phase 4

Improve spreadsheet/Base-specific extraction and add richer historical backfill controls.
