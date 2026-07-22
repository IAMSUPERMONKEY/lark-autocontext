"""WikiConnector: Feishu Wiki Space read/write wrapper built on lark-cli.

This module is the single gateway for all Feishu Wiki Space interactions in
lark-autocontext (spec section 3.1). Task 1 delivered the skeleton:

- ``WikiConnector`` class storing ``space_id`` / ``raw_node_token`` /
  ``agent_node_token`` / ``identity``.
- ``DocInfo`` and ``DocMeta`` data structures.
- ``_run_lark`` subprocess helper with 429 exponential-backoff retry
  (1s -> 2s -> 4s, max 3 retries).

Task 2 adds the read operations (spec section 3.1, read operations table):

- ``list_raw_docs`` / ``list_agent_docs`` -- list direct children of the
  raw / agent root nodes, with optional ``since`` incremental filter.
- ``list_wiki_subtree`` -- recursively list all descendants of a node.
- ``fetch_doc_content`` -- resolve node_token -> obj_token, fetch markdown
  and clean it via ``scanner.clean_feishu_content``.
- ``fetch_doc_meta`` -- resolve title / timestamps / creator / owner.

Task 3 adds the write operations (spec section 3.1, write operations table):

- ``create_doc`` -- create a new docx node under a parent node.
- ``update_doc`` -- update an existing docx's content (full replace).
- ``upload_attachment`` -- upload a binary file (e.g. viz.html) to a node.
- ``delete_doc`` / ``move_doc`` -- node lifecycle (Agent reorg).
- ``check_doc_changed`` -- detect whether a node was edited since a
  known timestamp (used by dual_storage pull flow).
"""
from __future__ import annotations

import subprocess
import json
import time
import os
import re
import yaml
import tempfile
import logging
from dataclasses import dataclass

# scanner lives in the same scripts/ directory and is importable whenever
# wiki_connector is used (tests add scripts/ to sys.path; the runtime cwd is
# scripts/). There is no circular import: scanner -> cli (stdlib only).
from scanner import clean_feishu_content

logger = logging.getLogger(__name__)


@dataclass
class DocInfo:
    """A document node listed from a Feishu Wiki Space.

    Fields mirror spec section 3.1 (data structures).
    """
    node_token: str          # Feishu node token (unique id)
    title: str
    obj_type: str            # docx / sheet / file
    modified_time: str       # ISO8601, used for incremental sync
    url: str                 # Feishu URL
    has_children: bool       # whether the node has child nodes


@dataclass
class DocMeta:
    """Metadata for a single Feishu document."""
    title: str
    created_time: str
    modified_time: str
    creator: str
    owner: str


class WikiConnector:
    """Feishu Wiki Space read/write wrapper based on lark-cli.

    All Feishu Wiki interactions go through this class. It stores the wiki
    space identity (space id + raw/agent root node tokens) and delegates the
    actual lark-cli calls to ``_run_lark``.

    Args:
        space_id: Feishu wiki space id.
        raw_node_token: root node token of the raw docs area.
        agent_node_token: root node token of the Agent-maintained area.
        identity: lark-cli identity (``"user"`` or ``"tenant"``). ``"user"``
            is required to read user-private documents.
    """

    def __init__(self, space_id: str, raw_node_token: str,
                 agent_node_token: str, identity: str = "user"):
        self.space_id = space_id
        self.raw_node_token = raw_node_token
        self.agent_node_token = agent_node_token
        self.identity = identity

    def _run_lark(self, args: list, as_json: bool = True,
                  retries: int = 3):
        """Run a lark-cli command via subprocess, with 429 retry.

        Args:
            args: lark-cli argument list (without the leading ``lark-cli``).
            as_json: if True, parse stdout as JSON and return a dict; else
                return the raw stdout string.
            retries: max number of retries on HTTP 429 (default 3, backoff
                sequence 1s -> 2s -> 4s).

        Returns:
            Parsed JSON object (``as_json=True``) or stdout string
            (``as_json=False``).

        Raises:
            RuntimeError: on non-429 errors, or after exhausting 429 retries.
        """
        cmd = ["lark-cli"] + list(args)

        # Inject --as <identity> for non-auth commands (mirrors cli.LarkCLI.run
        # so that user-private documents can be read).
        is_auth_cmd = len(args) > 0 and args[0] == "auth"
        already_has_as = "--as" in args
        if self.identity and not is_auth_cmd and not already_has_as:
            cmd.extend(["--as", self.identity])

        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30
        )

        if result.returncode != 0:
            stderr = result.stderr or ""
            if "429" in stderr and retries > 0:
                # Exponential backoff: retries 3 -> 2 -> 1 yields 1s, 2s, 4s.
                backoff = 2 ** (3 - retries)
                logger.warning(
                    "lark-cli 429 rate limited, retrying in %ds "
                    "(retries left: %d)",
                    backoff, retries,
                )
                time.sleep(backoff)
                return self._run_lark(args, as_json=as_json, retries=retries - 1)
            raise RuntimeError(
                f"lark-cli failed (exit={result.returncode}): {stderr.strip()}"
            )

        if as_json:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "lark-cli returned non-JSON output: "
                    f"{(result.stdout or '')[:200]}"
                ) from exc
        return result.stdout

    # ------------------------------------------------------------------
    # Task 2: read operations
    # ------------------------------------------------------------------

    def _fetch_all_nodes(self) -> list:
        """Fetch every node in the wiki space as a list of raw node dicts.

        Calls ``wiki +node-list --space-id <space> --page-all`` (a lark-cli
        shortcut whose stdout is JSON-as-plain-text) and parses the
        ``data.nodes`` array. This is the shared backing fetch for all
        ``list_*`` read operations and for node_token -> obj_token resolution.

        The ``--page-all`` flag may emit progress lines (e.g.
        ``"Found 6 node(s)"``) before the JSON payload. We strip any
        leading non-JSON text by finding the first ``{`` character.
        """
        output = self._run_lark(
            ["wiki", "+node-list", "--space-id", self.space_id, "--page-all"],
            as_json=False,
        )
        if isinstance(output, str):
            # Strip progress prefix lines (e.g. "Found 6 node(s)")
            # by finding the first '{' character.
            json_start = output.find("{")
            if json_start > 0:
                output = output[json_start:]
            elif json_start == -1:
                return []
            data = json.loads(output)
        else:
            data = output
        return data.get("data", {}).get("nodes", []) or []

    @staticmethod
    def _node_to_docinfo(node: dict) -> DocInfo:
        """Convert a raw wiki node dict into a :class:`DocInfo`."""
        node_token = node.get("node_token", "")
        return DocInfo(
            node_token=node_token,
            title=node.get("title", ""),
            obj_type=node.get("obj_type", ""),
            modified_time=node.get("obj_edit_time", ""),
            url=f"https://feishu.cn/wiki/{node_token}",
            has_children=bool(node.get("has_child", False)),
        )

    @staticmethod
    def _since_to_unix(since: str) -> str:
        """Normalize an incremental ``since`` filter to a Unix timestamp string.

        Accepts either an ISO 8601 string (converted to Unix seconds) or a
        bare Unix timestamp string (returned unchanged). Returns "" for a
        falsy input. Falls back to the raw string when an ISO 8601 value
        cannot be parsed, so comparison still degrades gracefully.
        """
        if not since:
            return ""
        since_str = str(since)
        # A pure Unix timestamp is all digits; ISO 8601 carries 'T'/'-'/':'.
        if since_str.isdigit():
            return since_str
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(since_str.replace("Z", "+00:00"))
            return str(int(dt.timestamp()))
        except Exception:
            return since_str

    def _fetch_children(self, parent_node_token: str) -> list:
        """Fetch direct children of a specific parent node.

        Uses ``wiki +node-list --parent-node-token`` which returns children
        at any depth (unlike ``--page-all`` which only returns root-level
        nodes). Falls back to ``_fetch_all_nodes()`` filtering when the
        parent_node_token is empty (root level).
        """
        if not parent_node_token:
            # Root level: use --page-all (returns root-level nodes).
            return self._fetch_all_nodes()
        output = self._run_lark(
            ["wiki", "+node-list", "--space-id", self.space_id,
             "--parent-node-token", parent_node_token],
            as_json=False,
        )
        if isinstance(output, str):
            json_start = output.find("{")
            if json_start > 0:
                output = output[json_start:]
            elif json_start == -1:
                return []
            data = json.loads(output)
        else:
            data = output
        return data.get("data", {}).get("nodes", []) or []

    def list_raw_docs(self, since: str = None) -> list:
        """List documents in the raw docs area.

        Returns the direct children of ``raw_node_token`` as :class:`DocInfo`.
        When ``raw_node_token`` is empty (root level), the Agent maintenance
        area node itself is excluded. When ``since`` is given (ISO 8601 or
        Unix timestamp), only nodes whose ``obj_edit_time`` is at or after
        ``since`` are returned.
        """
        nodes = self._fetch_children(self.raw_node_token)
        docs = [self._node_to_docinfo(n) for n in nodes
                if n.get("parent_node_token") == self.raw_node_token
                and n.get("node_token") != self.agent_node_token]
        if since:
            since_ts = self._since_to_unix(since)
            docs = [d for d in docs if str(d.modified_time) >= since_ts]
        return docs

    def list_agent_docs(self, since: str = None) -> list:
        """List documents in the Agent-maintained area.

        Returns the direct children of ``agent_node_token`` as
        :class:`DocInfo`. Uses ``--parent-node-token`` to query children
        directly (works at any depth, unlike ``--page-all``). Used to detect
        human edits in the Agent area and for dedup checks during sync.
        ``since`` filters by ``obj_edit_time`` exactly like
        :meth:`list_raw_docs`.
        """
        nodes = self._fetch_children(self.agent_node_token)
        docs = [self._node_to_docinfo(n) for n in nodes
                if n.get("parent_node_token") == self.agent_node_token]
        if since:
            since_ts = self._since_to_unix(since)
            docs = [d for d in docs if str(d.modified_time) >= since_ts]
        return docs

    def list_wiki_subtree(self, node_token: str) -> list:
        """List ALL descendants of ``node_token`` (recursive).

        Fetches the full node list once, then traverses
        ``parent_node_token`` relationships starting from ``node_token`` to
        collect every descendant. The ``node_token`` node itself is NOT
        included in the result.
        """
        nodes = self._fetch_all_nodes()
        # Map parent_node_token -> list of child node dicts for traversal.
        children_by_parent: dict = {}
        for n in nodes:
            children_by_parent.setdefault(
                n.get("parent_node_token", ""), []
            ).append(n)

        descendants: list = []
        stack = list(children_by_parent.get(node_token, []))
        while stack:
            current = stack.pop()
            descendants.append(current)
            stack.extend(
                children_by_parent.get(current.get("node_token", ""), [])
            )
        return [self._node_to_docinfo(n) for n in descendants]

    def _resolve_obj_token(self, node_token: str) -> str:
        """Resolve a wiki ``node_token`` to its underlying ``obj_token``.

        Uses ``wiki +node-get --token <node_token>`` for a direct lookup.
        This works for nodes at any depth in the tree, unlike
        ``_fetch_all_nodes()`` which only returns root-level nodes.

        Falls back to a ``_fetch_all_nodes()`` scan if the direct call fails
        (e.g. permission issues), so existing behaviour is preserved.

        Returns "" when the obj_token cannot be resolved.
        """
        # Primary path: direct node-get (works at any depth).
        try:
            output = self._run_lark(
                ["wiki", "+node-get", "--token", node_token],
                as_json=False,
            )
            if isinstance(output, str):
                json_start = output.find("{")
                if json_start >= 0:
                    output = output[json_start:]
            data = json.loads(output) if isinstance(output, str) else output
            obj_token = data.get("data", {}).get("obj_token", "")
            if obj_token:
                return obj_token
        except (RuntimeError, json.JSONDecodeError, ValueError) as exc:
            logger.debug(
                "_resolve_obj_token: node-get failed for %s, "
                "falling back to node-list scan: %s",
                node_token, exc,
            )

        # Fallback: scan the root-level node list (may miss child nodes).
        for n in self._fetch_all_nodes():
            if n.get("node_token") == node_token:
                return n.get("obj_token", "")
        logger.warning(
            "_resolve_obj_token: node %r not found in space %s",
            node_token, self.space_id,
        )
        return ""

    def fetch_doc_content(self, node_token: str) -> str:
        """Fetch a document's content as cleaned Markdown.

        Resolves ``node_token`` -> ``obj_token`` via the wiki node list, then
        fetches the doc markdown through ``docs +fetch``, and finally runs it
        through ``scanner.clean_feishu_content`` to strip residual Feishu
        HTML/private tags.
        """
        obj_token = self._resolve_obj_token(node_token)
        output = self._run_lark(
            ["docs", "+fetch", "--doc", obj_token, "--doc-format", "markdown"],
            as_json=False,
        )
        data = json.loads(output) if isinstance(output, str) else output
        content = (
            data.get("data", {}).get("document", {}).get("content", "")
        )
        return clean_feishu_content(content)

    def fetch_doc_meta(self, node_token: str) -> DocMeta:
        """Fetch metadata (title, timestamps, creator, owner) for a document.

        Performs three lark-cli calls:

        1. ``wiki +node-list`` to resolve ``node_token`` -> ``obj_token``.
        2. ``drive +inspect`` to resolve the title (defensively tries
           ``data.title`` then ``data.data.title``, falling back to the
           obj_token).
        3. ``docs +fetch --detail full`` to resolve ``created_time``,
           ``modified_time``, ``creator`` and ``owner`` (best-effort; any
           missing field defaults to "").

        Returns:
            A :class:`DocMeta`. Title never falls through to raise; the
            timestamp/owner fields default to "" when the detail fetch fails.
        """
        obj_token = self._resolve_obj_token(node_token)

        # --- title via drive +inspect ---
        title = obj_token
        try:
            output = self._run_lark(
                ["drive", "+inspect", "--url", obj_token, "--type", "docx"],
                as_json=False,
            )
            data = json.loads(output) if isinstance(output, str) else output
            title = (
                data.get("title")
                or data.get("data", {}).get("title")
                or obj_token
            )
        except (RuntimeError, json.JSONDecodeError) as exc:
            logger.warning(
                "fetch_doc_meta: drive +inspect failed for %s: %s",
                obj_token, exc,
            )

        # --- timestamps / creator / owner via docs +fetch --detail full ---
        created_time = ""
        modified_time = ""
        creator = ""
        owner = ""
        try:
            output = self._run_lark(
                ["docs", "+fetch", "--doc", obj_token, "--doc-format",
                 "markdown", "--detail", "full"],
                as_json=False,
            )
            data = json.loads(output) if isinstance(output, str) else output
            doc = data.get("data", {}).get("document", {})
            created_time = (
                doc.get("created_time_iso") or doc.get("created_time") or ""
            )
            modified_time = (
                doc.get("last_modified_time_iso")
                or doc.get("updated_time")
                or doc.get("modified_time") or ""
            )
            creator = doc.get("creator") or doc.get("creator_id") or ""
            owner = doc.get("owner") or doc.get("owner_id") or ""
        except (RuntimeError, json.JSONDecodeError) as exc:
            logger.warning(
                "fetch_doc_meta: docs +fetch --detail full failed for %s: %s",
                obj_token, exc,
            )

        return DocMeta(
            title=title,
            created_time=created_time,
            modified_time=modified_time,
            creator=creator,
            owner=owner,
        )

    # ------------------------------------------------------------------
    # Task 3: write operations
    # ------------------------------------------------------------------

    def create_doc(self, parent_node_token: str, title: str,
                   content_md: str) -> str:
        """Create a new Feishu docx under ``parent_node_token``.

        Two-step process:
        1. Create a wiki node via ``wiki +node-create`` (creates empty docx).
        2. Write content via ``docs +update --command overwrite``.

        Returns the newly created node_token.

        Args:
            parent_node_token: parent wiki node to create the doc under.
            title: document title.
            content_md: Markdown content to seed the new docx with.

        Returns:
            The new node_token (empty string when parsing fails).
        """
        # Step 1: Create the wiki node (empty docx with title).
        output = self._run_lark(
            ["wiki", "+node-create", "--space-id", self.space_id,
             "--parent-node-token", parent_node_token,
             "--obj-type", "docx", "--title", title],
            as_json=False,
        )
        # Parse node_token: JSON first, regex fallback.
        node_token = ""
        try:
            if isinstance(output, str):
                json_start = output.find("{")
                if json_start >= 0:
                    output = output[json_start:]
            data = json.loads(output) if isinstance(output, str) else output
            node_token = data.get("data", {}).get("node_token", "")
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass
        if not node_token:
            match = re.search(
                r'node[_-]?token["\s:]+([a-zA-Z0-9]+)',
                output if isinstance(output, str) else ""
            )
            if match:
                node_token = match.group(1)

        if not node_token:
            return ""

        # Step 2: Write content to the newly created document.
        # Use a relative path in cwd because lark-cli requires relative paths.
        temp_name = f".lark_tmp_{node_token}.md"
        try:
            with open(temp_name, "w", encoding="utf-8") as temp:
                temp.write(content_md)
            obj_token = self._resolve_obj_token(node_token)
            if obj_token:
                self._run_lark(
                    ["docs", "+update", "--doc", obj_token,
                     "--doc-format", "markdown", "--command", "overwrite",
                     "--content", "@" + temp_name],
                    as_json=False,
                )
        finally:
            try:
                os.unlink(temp_name)
            except OSError:
                pass

        return node_token

    def update_doc(self, node_token: str, content_md: str) -> None:
        """Update an existing docx content (full Markdown replace).

        Resolves ``node_token`` -> ``obj_token`` via the wiki node list, writes
        ``content_md`` to a temp file, and uploads it through
        ``docs +update --doc <obj_token> --doc-format markdown --command
        overwrite --content @<file>``. The temp file is always
        cleaned up in a ``finally`` block.

        Args:
            node_token: wiki node token of the document to update.
            content_md: new Markdown content (replaces the existing body).
        """
        # Use a relative path in cwd because lark-cli requires relative paths.
        temp_name = f".lark_tmp_{node_token}.md"
        try:
            with open(temp_name, "w", encoding="utf-8") as temp:
                temp.write(content_md)
            obj_token = self._resolve_obj_token(node_token)
            self._run_lark(
                ["docs", "+update", "--doc", obj_token,
                 "--doc-format", "markdown", "--command", "overwrite",
                 "--content", "@" + temp_name],
                as_json=False,
            )
        finally:
            try:
                os.unlink(temp_name)
            except OSError:
                pass

    def upload_attachment(self, parent_node_token: str, filename: str,
                          file_bytes: bytes) -> str:
        """Upload a binary file (e.g. ``viz.html``) under ``parent_node_token``.

        Writes ``file_bytes`` to a temp file and uploads it via
        ``drive +upload``. Returns the file_token of the uploaded file.

        The lark-cli response may be JSON
        (``{"data": {"file_token": "..."}}``) or plain text. JSON is tried
        first; on failure a regex fallback extracts the token.

        Args:
            parent_node_token: parent folder/node token to upload under.
            filename: file name for the uploaded resource.
            file_bytes: raw file content as bytes.

        Returns:
            The file_token (empty string when parsing fails).
        """
        # Use a relative path in cwd because lark-cli requires relative paths.
        temp_name = f".lark_tmp_{filename}"
        try:
            with open(temp_name, "wb") as temp:
                temp.write(file_bytes)
            output = self._run_lark(
                ["drive", "+upload", "--wiki-token", parent_node_token,
                 "--file", temp_name, "--name", filename],
                as_json=False,
            )
            # Parse file_token: JSON first, regex fallback.
            file_token = ""
            try:
                data = json.loads(output) if isinstance(output, str) else output
                file_token = data.get("data", {}).get("file_token", "")
            except (json.JSONDecodeError, AttributeError, TypeError):
                pass
            if not file_token:
                match = re.search(
                    r'(file[_-]?token)["\s:]+([a-zA-Z0-9]+)', output
                )
                if match:
                    file_token = match.group(2)
            return file_token
        finally:
            try:
                os.unlink(temp_name)
            except OSError:
                pass

    def delete_doc(self, node_token: str) -> None:
        """Delete a wiki node (Agent reorg cleanup).

        Args:
            node_token: wiki node token to delete.
        """
        self._run_lark(
            ["wiki", "+node-delete", "--space-id", self.space_id,
             "--node-token", node_token, "--obj-type", "wiki", "--yes"],
            as_json=False,
        )

    def move_doc(self, node_token: str, new_parent_token: str) -> None:
        """Move a wiki node to a new parent (topic reorg).

        Args:
            node_token: wiki node token to move.
            new_parent_token: target parent node token.
        """
        self._run_lark(
            ["wiki", "+move-node", "--space-id", self.space_id,
             "--node-token", node_token,
             "--target-parent-token", new_parent_token],
            as_json=False,
        )

    def check_doc_changed(self, node_token: str,
                          last_known_time: str) -> bool:
        """Check whether a wiki node was edited after ``last_known_time``.

        Fetches the full node list, finds the node matching ``node_token``,
        and compares its ``obj_edit_time`` against ``last_known_time``.
        ``last_known_time`` is normalized to a Unix timestamp via
        :meth:`_since_to_unix` (ISO 8601 is accepted). String comparison of
        same-length Unix timestamps is equivalent to numeric comparison.

        Returns ``True`` when ``obj_edit_time > last_known_time`` (i.e. the
        document was edited after the known timestamp), ``False`` otherwise.
        When the node is not found, logs a warning and returns ``False``
        (spec error handling: skip, do not raise).

        Args:
            node_token: wiki node token to check.
            last_known_time: Unix timestamp or ISO 8601 string of the last
                known edit time.
        """
        for n in self._fetch_all_nodes():
            if n.get("node_token") == node_token:
                current_time = str(n.get("obj_edit_time", ""))
                known_time = self._since_to_unix(last_known_time)
                return current_time > known_time
        logger.warning(
            "check_doc_changed: node %r not found in space %s",
            node_token, self.space_id,
        )
        return False


# ------------------------------------------------------------------
# Task 4: OKF ↔ Feishu docx conversion (module-level functions)
#
# Feishu docx does not carry YAML frontmatter. When pushing an OKF document
# to Feishu the frontmatter is replaced by a human-readable emoji metadata
# header (spec section 3.1); when pulling Feishu edits back, the header is
# stripped and the body is cleaned. These five pure-string functions are the
# shared conversion layer used by dual_storage.py (Task 5).
# ------------------------------------------------------------------


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from OKF Markdown content.

    Frontmatter is delimited by ``---`` at the start of the file. The YAML
    block between the opening and closing ``---`` is parsed with
    :func:`yaml.safe_load`. Returns ``(frontmatter_dict, body_str)`` where
    ``body_str`` is everything after the closing ``---`` (leading newlines
    stripped). When no frontmatter is present (the content does not start
    with ``---``), returns ``({}, content)``.

    Edge cases:
      - Empty frontmatter (``---\\n---``) -> ``({}, "")``.
      - A ``timestamp`` ISO 8601 value is parsed by PyYAML into a
        :class:`datetime.datetime`; callers that need a string slice should
        coerce via :func:`str` first.
    """
    if not content.startswith("---"):
        return ({}, content)
    lines = content.split("\n")
    # Locate the closing ``---`` delimiter (first one after the opening line).
    closing_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            closing_idx = i
            break
    if closing_idx is None:
        # Opening delimiter without a closing one -> not valid frontmatter.
        return ({}, content)
    yaml_block = "\n".join(lines[1:closing_idx])
    body = "\n".join(lines[closing_idx + 1:])
    fm = yaml.safe_load(yaml_block) if yaml_block.strip() else {}
    if fm is None or not isinstance(fm, dict):
        fm = {}
    return (fm, body.lstrip("\n"))


def generate_metadata_header(frontmatter: dict) -> str:
    """Generate a human-readable emoji metadata header from frontmatter.

    Mirrors spec section 3.1 (OKF → 飞书 docx 转换): the YAML frontmatter is
    not written to Feishu, so this header is the human-readable substitute
    placed at the top of the Feishu document body. Each line only includes
    fields present in ``frontmatter`` (empty/missing fields are skipped).

    Layout::

        📝 类型：{type} | 项目：{project} | 标签：{tags}
        👥 相关人员：{people} | 📅 {date}
        🔗 原始文档：{resource}
        ---

    ``type`` is always included (default ``"Other"``). ``tags`` / ``people``
    accept either a list (joined with ``", "``) or a plain string. ``date``
    is extracted from ``timestamp`` as the first 10 characters (``YYYY-MM-DD``
    from an ISO 8601 value, coerced via :func:`str` so a PyYAML-parsed
    :class:`~datetime.datetime` is handled too). The returned string ends
    with ``---`` and has NO trailing newline (the caller adds the
    ``"\\n\\n"`` separator before the body).
    """
    # Line 1: type (always, default "Other") | project | tags
    line1_parts = [f"📝 类型：{frontmatter.get('doc_type') or frontmatter.get('type') or 'Other'}"]
    if frontmatter.get("project"):
        line1_parts.append(f"项目：{frontmatter['project']}")
    if frontmatter.get("tags"):
        tags = frontmatter["tags"]
        tags_str = ", ".join(tags) if isinstance(tags, list) else str(tags)
        line1_parts.append(f"标签：{tags_str}")

    # Line 2: people | date (from timestamp, first 10 chars = YYYY-MM-DD)
    line2_parts = []
    if frontmatter.get("people"):
        people = frontmatter["people"]
        people_str = ", ".join(people) if isinstance(people, list) else str(people)
        line2_parts.append(f"👥 相关人员：{people_str}")
    date = str(frontmatter.get("timestamp") or "")[:10]
    if date:
        line2_parts.append(f"📅 {date}")

    lines = [" | ".join(line1_parts)]
    if line2_parts:
        lines.append(" | ".join(line2_parts))
    if frontmatter.get("resource"):
        lines.append(f"🔗 原始文档：{frontmatter['resource']}")
    lines.append("---")
    return "\n".join(lines)


def strip_metadata_header(content: str) -> str:
    """Remove the emoji metadata header from Feishu content.

    The header starts with ``📝`` and ends with ``---`` on its own line. This
    function finds the first ``📝`` marker, then the next ``---`` line after
    it, and returns everything after that ``---`` line (leading
    whitespace/newlines stripped). When no ``📝`` is present, ``content`` is
    returned unchanged (there is no header to strip). If a ``📝`` is found but
    no closing ``---`` follows, the content is also returned unchanged.
    """
    idx = content.find("📝")
    if idx == -1:
        return content
    after = content[idx:]
    lines = after.split("\n")
    closing = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            closing = i
            break
    if closing is None:
        return content
    body = "\n".join(lines[closing + 1:])
    return body.lstrip("\n")


def _preserve_image_alt_text(body: str) -> str:
    """Convert long image alt text into caption lines so Feishu preserves them.

    Feishu's ``docs +update --command overwrite`` strips image alt text during
    Markdown import: ``![long description](url)`` becomes ``![](url)``. To
    prevent content loss, this function converts alt text longer than 20
    characters into a separate italic caption line placed below the image.

    Short alt text (<=20 chars, e.g. "logo", "diagram") is left intact --
    it is unlikely to carry meaningful information and Feishu may preserve
    it for accessibility purposes.
    """
    def _replace_image(m):
        alt = m.group(1)
        url = m.group(2)
        if len(alt) > 20:
            return f"![]({url})\n\n*📷 图片描述：{alt}*"
        return m.group(0)

    return re.sub(
        r'!\[([^\]]*)\]\(([^)]+)\)', _replace_image, body
    )


def okf_to_feishu_content(okf_content: str) -> str:
    """Convert OKF Markdown into Feishu-displayable content.

    Full conversion pipeline:

    1. Parse the YAML frontmatter with :func:`_parse_frontmatter`.
    2. Generate the emoji metadata header with :func:`generate_metadata_header`.
    3. Preserve image alt text as caption lines (Feishu strips alt text
       during Markdown import -- see :func:`_preserve_image_alt_text`).
    4. Concatenate ``header + "\\n\\n" + body``.
    """
    fm, body = _parse_frontmatter(okf_content)
    header = generate_metadata_header(fm)
    body = _preserve_image_alt_text(body)
    return header + "\n\n" + body


def feishu_to_okf_body(feishu_content: str) -> str:
    """Convert Feishu content back to an OKF body (without frontmatter).

    Strips the emoji metadata header with :func:`strip_metadata_header`, then
    cleans residual Feishu HTML/private tags via
    :func:`scanner.clean_feishu_content`. The caller is responsible for
    re-attaching the local frontmatter (human edits never touch frontmatter).
    """
    stripped = strip_metadata_header(feishu_content)
    return clean_feishu_content(stripped)
