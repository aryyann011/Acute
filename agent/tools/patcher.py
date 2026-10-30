import shutil
import textwrap
import ast
from pathlib import Path
import subprocess
from loguru import logger
from tree_sitter import Language, Parser, Query, QueryCursor
import tree_sitter_python as tspy

class CodePatcher:
    def __init__(self):
        self.py_language = Language(tspy.language())
        self.parser = Parser(self.py_language)

    def _backup_file(self, file_path: Path) -> Path:
        """Creates a .bak copy of the file before we perform surgery."""
        backup_path = file_path.with_suffix(file_path.suffix + ".bak")
        shutil.copy2(file_path, backup_path)
        logger.info(f"Safety First: Created backup at {backup_path}")
        return backup_path

    def snapshot_file(self, file_path: str) -> Path | None:
        """
        Transaction snapshot: copies the file to a .bak ONCE, before any edits in the
        batch touch it. Returns None if the file does not exist.
        """
        path = Path(file_path)
        if not path.exists():
            logger.error(f"Cannot snapshot, file not found: {path}")
            return None
        return self._backup_file(path)

    def restore_file(self, file_path: str, backup_path: Path) -> bool:
        """Rollback: copies the snapshot back over the (possibly modified) original."""
        try:
            shutil.copy2(backup_path, Path(file_path))
            logger.info(f"Restored {file_path} from {backup_path}")
            return True
        except OSError as e:
            logger.error(f"Rollback failed for {file_path}: {e}")
            return False

    def _line_prefix_at(self, file_bytes: bytes, byte_index: int) -> str:
        """
        Returns the whitespace (indentation) written in the file on the line containing
        byte_index, up to byte_index. This is the indentation the REAL file already owns.
        """
        step = byte_index - 1
        prefix = []
        while step >= 0 and file_bytes[step] in (0x20, 0x09):  # space or tab
            prefix.append(chr(file_bytes[step]))
            step -= 1
        return "".join(reversed(prefix))

    def _normalize_incoming_code(self, new_code: str, base_prefix: str) -> str:
        """
        Gate step 1-3: strip Gemini's uniform leading indent, prove the result is valid
        Python, then shift interior lines by the indentation the file itself uses.
        Raises ValueError when the incoming code is not valid Python (gate step 2).
        """
        dedented = textwrap.dedent(new_code)

        try:
            ast.parse(dedented)
        except SyntaxError as e:
            raise ValueError(f"Generated code is not valid Python: line {e.lineno}: {e.msg}")

        if not base_prefix:
            return dedented

        shifted_lines = []
        for position, line in enumerate(dedented.splitlines(keepends=True)):
            if position == 0 or not line.strip():   # first line meets the file's existing indent; blanks stay blank
                shifted_lines.append(line)
            else:
                shifted_lines.append(f"{base_prefix}{line}")   # dedent already carries the correct RELATIVE indent
        return "".join(shifted_lines)

    def patch_file(self, file_path: str, target_function: str, new_code: str, create_backup: bool = True) -> bool:
        """
        Executes the targeted AST byte-splice.
        create_backup=False is used inside transactions that snapshot files separately.
        """
        path = Path(file_path)

        if not path.exists():
            logger.error(f"File not found: {path}")
            return False

        backup_path = self._backup_file(path) if create_backup else None
        with open(path, "rb") as f:
            file_bytes = f.read()

        tree = self.parser.parse(file_bytes)

        query_string = f"""
        (function_definition
          name: (identifier) @name
          (#eq? @name "{target_function}")
        ) @target_node
        """
        
        try:
            query = Query(self.py_language, query_string)
        except Exception as e:
            logger.error(f"Failed to compile Tree-sitter query: {e}")
            return False

        cursor = QueryCursor(query)
        captures = cursor.captures(tree.root_node)
        
        target_nodes = captures.get("target_node", [])

        if not target_nodes:
            logger.error(
                f"Could not find function '{target_function}' in {path}. Aborting patch."
            )
            return False

        target_node = target_nodes[0]

        if not target_node:
            logger.error(f"Could not find function '{target_function}' in {path}. Aborting patch.")
            return False

        start_byte = target_node.start_byte
        end_byte = target_node.end_byte

        logger.debug(f"Target '{target_function}' found. Slicing from byte {start_byte} to {end_byte}.")

        # Gate 1-3: strip incoming indent, validate it as Python, re-indent to the file's convention
        base_prefix = self._line_prefix_at(file_bytes, start_byte)
        try:
            normalized_code = self._normalize_incoming_code(new_code, base_prefix)
        except ValueError as e:
            logger.error(f"Patch rejected before write ({path}): {e}")
            return False
        normalized_bytes = normalized_code.encode("utf-8")

        candidate_bytes = file_bytes[:start_byte] + normalized_bytes + file_bytes[end_byte:]

        # Gate 4: prove the ENTIRE spliced file still parses before anything touches the disk
        try:
            ast.parse(candidate_bytes.decode("utf-8", errors="replace"))
        except SyntaxError as e:
            logger.error(
                f"Patch rejected before write ({path}): splicing '{target_function}' would "
                f"break the file's syntax (line {e.lineno}: {e.msg}). No changes written."
            )
            return False

        with open(path, "wb") as f:
            f.write(candidate_bytes)
            
        logger.success(f"Successfully patched '{target_function}' in {path}")

        if backup_path is None:
            return True

        try:
            logger.info("Launching VS Code diff viewer...")
            import sys
            
            vscode_cmd = "code.cmd" if sys.platform == "win32" else "code"
            
            subprocess.run([vscode_cmd, "--diff", str(backup_path), str(path)], check=False)
        except FileNotFoundError:
            logger.warning("VS Code CLI ('code') not found. Skipping visual diff.")

        return True