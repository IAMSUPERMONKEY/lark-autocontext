import subprocess
import json
import re
import os
import sys

class LarkCLI:
    def __init__(self, config_path=None):
        """Load configuration automatically."""
        if config_path is None:
            config_path = os.path.join(os.path.dirname(__file__), "config.json")
        
        self.config = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    self.config = json.load(f)
            except Exception:
                pass

        # Identity: "user" (default, can read user's private docs) or "tenant" (bot identity).
        # User identity is required for most enterprise docs; bot identity often returns
        # permission errors (e.g. lark-cli error 3380004) for user-owned private documents.
        self.identity = self.config.get("identity", "user")

    def get_base_token(self):
        return self.config.get("base_token")

    def check_auth(self):
        """Quick auth check — returns True if token is valid, raises if expired."""
        try:
            output = self.run(["auth", "status"], as_json=False)
            data = json.loads(output)
            # Check if there's a note about not being logged in
            note = data.get("note", "")
            if "not logged in" in note.lower() or "expired" in note.lower():
                raise Exception(
                    "❌ lark-cli not authenticated.\n"
                    "   Run: lark-cli auth login --recommend --no-wait\n"
                    "   Then open the verification URL and complete login."
                )
            return True
        except json.JSONDecodeError:
            return True  # If output isn't JSON, assume it's working
    def run(self, command_args, as_json=True):
        """Run lark-cli command and return output.

        Automatically injects `--as <identity>` (default: user) so that user-owned
        private documents can be read. Skips injection for `auth ...` commands
        and when caller already specifies `--as`.
        """
        cmd = ["lark-cli"] + command_args

        # Inject identity flag unless caller already provided one, or it's an auth command.
        is_auth_cmd = len(command_args) > 0 and command_args[0] == "auth"
        already_has_as = "--as" in command_args
        if self.identity and not is_auth_cmd and not already_has_as:
            cmd.extend(["--as", self.identity])

        if as_json and "--format" not in " ".join(command_args):
            # Check if the command supports --format json (API commands usually do)
            # Shortcuts (starting with +) usually do NOT support --format json
            if any(arg.startswith("+") for arg in command_args):
                pass # Shortcuts usually return plain text
            else:
                cmd.append("--format")
                cmd.append("json")
        
        # Cross-platform: Windows needs shell=True to find npm global .cmd scripts
        use_shell = sys.platform == "win32"
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", shell=use_shell
        )
        if result.returncode != 0:
            raise Exception(f"CLI Error: {result.stderr}")
        if result.stderr:
            print(f"[WARN] {result.stderr.strip()}")
        return result.stdout

    def create_base(self, name, folder_token=None):
        """Create a new Feishu Base."""
        output = self.run(["base", "+base-create", "--name", name])
        # Parse output: "Base created: app_token: xxx" or similar
        # Let's assume the output contains the app_token or we can search for it.
        # A better way is to use `lark-cli base +base-create` which prints the token.
        match = re.search(r'(app_token|token)[\s:]+([a-zA-Z0-9]+)', output)
        if match:
            return match.group(2)
        # If regex fails, try to parse the whole output if it's JSON-like or just return raw
        return output.strip()

    def create_table(self, app_token, table_name="Context"):
        """Create a table in the Base."""
        return self.run(["base", "+table-create", "--base-token", app_token, "--name", table_name])

    def create_field(self, app_token, table_id, field_type, field_name, options=None):
        """Create a field in the table."""
        payload = {"name": field_name, "type": field_type}
        if options:
            payload["options"] = [{"name": o} for o in options]
        
        cmd = ["base", "+field-create", "--base-token", app_token, "--table-id", table_id,
               "--json", json.dumps(payload)]
        return self.run(cmd)

    def create_view(self, app_token, table_id, view_name, view_type, group_by=None):
        """Create a view (Kanban, Table, etc.)."""
        payload = {"name": view_name, "type": view_type}
        if group_by:
            payload["group_by"] = group_by
            
        cmd = ["base", "+view-create", "--base-token", app_token, "--table-id", table_id,
               "--json", json.dumps(payload)]
        return self.run(cmd)

    def list_records(self, app_token, table_id, filter_field=None, filter_value=None):
        """List records, optionally filtered."""
        cmd = ["base", "+record-list", "--base-token", app_token, "--table-id", table_id]
        return self.run(cmd)

    def upsert_record(self, app_token, table_id, record_data, match_field="文档 Token"):
        """Create or update a record."""
        cmd = ["base", "+record-upsert", "--base-token", app_token, "--table-id", table_id,
               "--json", json.dumps(record_data)]
        return self.run(cmd)

    def fetch_doc(self, doc_token, doc_format="markdown"):
        """Fetch Feishu doc content. Returns parsed content string."""
        cmd = ["docs", "+fetch", "--doc", doc_token, "--doc-format", doc_format]
        output = self.run(cmd, as_json=False)
        # Parse JSON output to extract content
        try:
            data = json.loads(output)
            return data.get("data", {}).get("document", {}).get("content", "")
        except:
            return output

    def fetch_sheet(self, sheet_token, sheet_id, range_str=None):
        """Fetch Feishu sheet content. Returns parsed content string."""
        cmd = ["sheets", "+range-read", "--sheet", sheet_token, "--sheet-id", sheet_id]
        if range_str:
            cmd.extend(["--range", range_str])
        output = self.run(cmd, as_json=False)
        try:
            data = json.loads(output)
            return json.dumps(data.get("data", {}).get("value", ""), ensure_ascii=False)
        except:
            return output

    def fetch_wiki_tree(self, space_id):
        """Fetch all nodes in a wiki space. Returns list of node dicts."""
        output = self.run(["wiki", "+node-list", "--space-id", space_id, "--page-all"], as_json=False)
        try:
            data = json.loads(output)
            return data.get("data", {}).get("nodes", [])
        except:
            return []

    def fetch_folder_files(self, folder_token):
        """Search files in a Feishu folder. Returns list of normalized file dicts."""
        output = self.run(["drive", "+search", "--folder-tokens", folder_token], as_json=False)
        try:
            data = json.loads(output)
            results = data.get("data", {}).get("results", [])
            files = []
            for r in results:
                meta = r.get("result_meta", {})
                files.append({
                    "type": meta.get("doc_types", ""),
                    "token": meta.get("token", ""),
                    "name": r.get("title_highlighted", meta.get("token", "")),
                    "url": meta.get("url", ""),
                    "modified_time": meta.get("update_time_iso", ""),
                })
            return files
        except:
            return []

    def fetch_folder_files_since(self, folder_token, since):
        """Search files edited since `since` in the folder."""
        args = ["drive", "+search", "--folder-tokens", folder_token,
                "--edited-since", since]
        output = self.run(args, as_json=False)
        try:
            data = json.loads(output)
            results = data.get("data", {}).get("results", [])
            files = []
            for r in results:
                meta = r.get("result_meta", {})
                files.append({
                    "type": meta.get("doc_types", ""),
                    "token": meta.get("token", ""),
                    "name": r.get("title_highlighted", meta.get("token", "")),
                    "url": meta.get("url", ""),
                    "edit_time": meta.get("update_time_iso", ""),
                })
            return files
        except Exception:
            return []

    def fetch_wiki_changed_since(self, space_id, since):
        """Fetch wiki nodes whose underlying docs changed since `since`."""
        nodes = self.fetch_wiki_tree(space_id)
        changed = []
        for n in nodes:
            edit_time = n.get("obj_edit_time") or n.get("edit_time", "")
            if edit_time and edit_time >= since:
                changed.append({
                    "token": n.get("obj_token", ""),
                    "url": f"https://feishu.cn/wiki/{n.get('node_token', '')}",
                    "name": n.get("title", ""),
                    "edit_time": edit_time,
                })
        return changed

    def fetch_doc_metadata(self, doc_token):
        """Fetch doc metadata (title, edited_time, etc.) via drive +inspect."""
        # Get title from drive +inspect
        title = doc_token
        try:
            output = self.run(["drive", "+inspect", "--url", doc_token,
                               "--type", "docx"], as_json=False)
            data = json.loads(output)
            title = data.get("title", doc_token)
        except Exception:
            pass

        # Try to get edited_time from docs +fetch (best-effort)
        edited_time = ""
        try:
            output = self.run(["docs", "+fetch", "--doc", doc_token,
                               "--doc-format", "markdown", "--detail", "full"], as_json=False)
            data = json.loads(output)
            doc = data.get("data", {}).get("document", {})
            edited_time = (doc.get("last_modified_time_iso")
                           or doc.get("updated_time")
                           or doc.get("revision_id_iso")
                           or "")
        except Exception:
            pass

        return {"title": title, "edited_time": edited_time}

    def fetch_doc_title(self, doc_token):
        """Fetch just the title of a doc. Returns title string."""
        output = self.run(["docs", "+fetch", "--doc", doc_token, "--doc-format", "markdown"], as_json=False)
        try:
            data = json.loads(output)
            return data.get("data", {}).get("document", {}).get("title", doc_token)
        except:
            return doc_token
