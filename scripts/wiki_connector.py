"""WikiConnector: Feishu Wiki Space read/write wrapper built on lark-cli.

This module is the single gateway for all Feishu Wiki Space interactions in
lark-autocontext (spec section 3.1). Task 1 delivers the skeleton only:

- ``WikiConnector`` class storing ``space_id`` / ``raw_node_token`` /
  ``agent_node_token`` / ``identity``.
- ``DocInfo`` and ``DocMeta`` data structures.
- ``_run_lark`` subprocess helper with 429 exponential-backoff retry
  (1s -> 2s -> 4s, max 3 retries).

Read/write operations (``list_raw_docs``, ``fetch_doc_content``,
``create_doc``, ...) are added in Tasks 2-4.
"""
import subprocess
import json
import time
import logging
from dataclasses import dataclass

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
