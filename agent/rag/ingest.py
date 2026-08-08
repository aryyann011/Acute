from pathlib import Path
from loguru import logger
import tree_sitter_python as tspy
import tree_sitter_javascript as tsjs
from tree_sitter import Language, Parser

class CodeChunker:
    def __init__(self):
        self.chunks = []
        self.py_parser = Parser(Language(tspy.language()))
        self.js_parser = Parser(Language(tsjs.language()))

    def _read_file(self, path: Path) -> str:
        try:
            with open(path, "r", encoding="utf-8") as file:
                return file.read()
        except Exception as e:
            logger.error(f"Failed to read {path} : {e}")
            return ""

    def _get_parser(self, path: Path) -> Parser | None:
        """Helper to return the right parser based on file extension"""
        if path.suffix == ".py":
            return self.py_parser
        elif path.suffix in [".js", ".ts", ".jsx", ".tsx"]:
            return self.js_parser
        return None

    def _is_js_arrow_function_assignment(self, node) -> bool:
        """Helper to detect if a const/let is actually a function assignment."""
        if node.type not in ["lexical_declaration", "variable_declaration"]:
            return False
        for child in node.children:
            if child.type == "variable_declarator":
                for subchild in child.children:
                    if subchild.type == "arrow_function":
                        return True
        return False

    def _get_node_name(self, node, content_bytes: bytes) -> str:
        """Helper to find the identifier (name) of a function or class"""
        if node.type in ["decorated_definition", "export_statement", "lexical_declaration", "variable_declaration"]:
            for child in node.children:
                if child.type in ["function_definition", "class_definition", "function_declaration", "class_declaration", "arrow_function"]:
                    node = child # Reassign 'node' to the inner element and continue
                    break

        for child in node.children:
            if child.type == "identifier":
                return content_bytes[child.start_byte:child.end_byte].decode('utf8')

        if node.type in ["lexical declaration", "variable declaration"]:
            for child in node.children:
                if child.type == "variable_declarator":
                    for subchild in child.children:
                        if subchild.type == "identifier":
                            return content_bytes[subchild.start_byte:subchild.end.byte].decode('utf8')
                
        return "unknown"

    def process_files(self, file_paths: list[Path]) -> list[dict]:
        logger.info(f"Chunking {len(file_paths)} files...")

        for path in file_paths:
            content = self._read_file(path)
            if not content:
                continue

            parser = self._get_parser(path)
            if not parser:
                continue

            content_bytes = bytes(content, "utf8")
            tree = parser.parse(content_bytes)
            root_node = tree.root_node

            globals_text = []
            for node in root_node.children:
                if node.type in ["lexical declaration", "variable declaration"]:
                    if self._is_js_arrow_function_assignment(node):
                        continue
                
                if node.type in ["import_statement", "import_from_statement", "expression_statement", "lexical_declaration"]:
                    globals_text.append(node.text.decode('utf8'))

            file_header = ""
            if globals_text:
                file_header = "\n".join(globals_text) + "\n\n# --- END GLOBALS ---\n\n"

            self._extract_chunks(root_node, path, content_bytes, file_header)

        logger.success(f"Successfully generated {len(self.chunks)} semantic chunks")
        return self.chunks

    def _extract_chunks(self, node, path: Path, content_bytes: bytes, file_header: str, class_context: str = ""):
        """Recursively parses AST to unwrap classes and grab pure functions"""

        target_types = [
            "function_definition", 
            "decorated_definition", 
            "function_declaration", 
            "method_definition", 
            "arrow_function"
        ]

        if node.type in target_types:
            chunk_content = file_header
            if class_context:
                chunk_content += f"# Class Context: {class_context}\n"
            chunk_content += content_bytes[node.start_byte:node.end_byte].decode('utf8')

            chunk = {
                "filepath": str(path),
                "name": self._get_node_name(node, content_bytes),
                "type": node.type,
                "start_line": node.start_point.row + 1,
                "end_line": node.end_point.row + 1,
                "content": chunk_content
            }
            self.chunks.append(chunk)
            return

        class_types = ["class_definition", "class_declaration"]
        if node.type in class_types:
            class_name = self._get_node_name(node, content_bytes)
            for child in node.children:
                # 'block' for Python, 'class_body' for JS/TS
                if child.type in ["block", "class_body"]:
                    for method_node in child.children:
                        self._extract_chunks(method_node, path, content_bytes, file_header, class_context=class_name)
            return

        for child in node.children:
            self._extract_chunks(child, path, content_bytes, file_header, class_context)