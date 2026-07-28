from pathlib import Path
from loguru import logger
import tree_sitter_python as tspy
import tree_sitter_javascript as tsjs
from tree_sitter import Language, Parser

class CodeChunker:
    def __init__(self, file_paths:list[Path]):
        self.file_paths = file_paths
        self.chunks = []

        self.py_parser = Parser(Language(tspy.language()))
        self.js_parser = Parser(Language(tsjs.language()))

    def _read_file(self, path:Path) ->str:
        try:
            with open(path, "r", encoding="utf-8") as file:
                return file.read()
        except Exception as e:
            logger.error(f"Failed to read {path} : {e}")
            return ""

    def _get_parser(self, path:Path) -> Parser | None:
        """Helper to return the right parser based on file extension"""
        if path.suffix == ".py":
            return self.py_parser
        elif path.suffix in [".js", ".ts"]:
            return self.js_parser
        return None

    def _get_node_name(self, node) -> str:
        """Helper to find the identifier (name) of a function or class"""
        for child in node.children:
            if child.type == "identifier":
                return child.text.decode('utf8')
            return "unknown"

    def process_files(self) -> list[dict]:
        logger.info(f"chunking {len(self.file_paths)} files...")

        for path in self.file_paths:
            content = self._read_file(path)
            if not content:
                continue

            parser = self._get_parser(path)
            if not parser:
                continue

            tree = parser.parse(bytes(content, "utf8"))
            root_node = tree.root_node

            globals_text = []
            for node in root_node.children:
                if node.type in ["import_statement", "import_from_statement", "expression_statement", "lexical_statement"]:
                    globals_text.append(node.text.decode('utf8'))

            file_header = ""
            if globals_text:
                file_header = "\n".join(globals_text) + "\n\n# --- END GLOBALS --- \n\n"

            for node in root_node.children:
                if node.type in ["function_defination", "class_defination", "function_declaration"]:
                    chunk_content = file_header + node.text.decode('utf8')

                    chunk = {
                        "filepaths" : str(path),
                        "name" : self._get_node_name(node),
                        "type" : node.type,
                        "start_line" : node.start_point.row + 1,
                        "end_line" : node.end_point.row+1,
                        "content" : chunk.content
                    }

                    self.chunks.append(chunk)

        logger.success(f"Successfully generated {len(self.chunks)} semantic chunks")
        return self.chunks
