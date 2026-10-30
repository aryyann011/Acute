import shutil
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

        new_code_bytes = new_code.encode("utf-8")

        patched_bytes = file_bytes[:start_byte] + new_code_bytes + file_bytes[end_byte:]

        with open(path, "wb") as f:
            f.write(patched_bytes)
            
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