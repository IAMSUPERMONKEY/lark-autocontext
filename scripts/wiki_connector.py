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

Write operations (``create_doc``, ``update_doc``, ...) are added in Tasks 3-4.
"""
from __future__ import annotations

import subprocess
import json
import time
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
        """
        output = self._run_lark(
            ["wiki", "+node-list", "--space-id", self.space_id, "--page-all"],
            as_json=False,
        )
        data = json.loads(output) if isinstance(output, str) else output
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

    def list_raw_docs(self, since: str = None) -> list:
        """List documents in the raw docs area.

        Returns the direct children of ``raw_node_token`` as :class:`DocInfo`.
        When ``since`` is given (ISO 8601 or Unix timestamp), only nodes whose
        ``obj_edit_time`` is at or after ``since`` are returned.
        """
        nodes = self._fetch_all_nodes()
        docs = [self._node_to_docinfo(n) for n in nodes
                if n.get("parent_node_token") == self.raw_node_token]
        if since:
            since_ts = self._since_to_unix(since)
            docs = [d for d in docs if str(d.modified_time) >= since_ts]
        return docs

    def list_agent_docs(self, since: str = None) -> list:
        """List documents in the Agent-maintained area.

        Returns the direct children of ``agent_node_token`` as
        :class:`DocInfo`. Used to detect human edits in the Agent area.
        ``since`` filters by ``obj_edit_time`` exactly like
        :meth:`list_raw_docs`.
        """
        nodes = self._fetch_all_nodes()
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

        The wiki node list maps each node_token to the obj_token of the
        backing doc/sheet/file. Returns "" when the node is not found.
        """
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
