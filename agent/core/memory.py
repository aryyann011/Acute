from typing import Dict, List
from pydantic import BaseModel, Field, ValidationError
from loguru import logger

class CodeChunk(BaseModel):
    """
    strict validation schema for data coming out of Qdrant
    Ensures no currupted data (missing keys or empty strings) poisons the LLM prompt.
    """
    filepath: str = Field(min_length=1)
    name: str = Field(min_length=1)
    content: str = Field(min_length=1)

class ContextManager:
    def assemble_xml_context(self, raw_chunks: List[Dict[str, Any]]) -> str:
        """
        Validate raw database chunks and concatenates them into single XML document.
        Drops malformed chunks silently to protect prompt intergrity.
        """

        if not raw_chunks:
            logger.warning("No chunks provided to the context manager. Returning empty context.")
            return "<codebase_context>\n<codebase_context>"

        valid_xml_blocks = []

        for raw_dict in raw_chunks:
            try:
                chunk = CodeChunk.model_validate(raw_dict)

                xml_block = f'<chunk filepath="{chunk.filepath}" name="{chunk.name}">\n{chunk.content}\n</chunk>'
                valid_xml_blocks.append(xml_block)

            except ValidationError as e:
                logger.warning(f"Dropped malformed chunk from context. Error: {e}")
                return "<codebase_context>\n</codebase_context>"

        if not valid_xml_blocks:
            logger.error("All retrieved chunks were malformed. Context is empty")
            return "<codebase_context>\n</codebase_context>"


        joined_chunks = "\n\n".join(valid_xml_blocks)
        final_xml = f"<codebase_context>\n{joined_chunks}\n</codebase_context>"
        
        logger.debug(f"Successfully assembled XML context with {len(valid_xml_blocks)} chunks.")
        
        return final_xml