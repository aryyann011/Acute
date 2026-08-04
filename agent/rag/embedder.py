import copy
from typing import List, Dict, Any
from loguru import logger
from fastembed import TextEmbedding
from tokenizers import Tokenizer

class VectorEmbedder:
    def __init__(self):
        logger.info("Initialising Tokenizer and FastEmbed models (BAAI/bge-small-en-v1.5)...")
        self.tokenizer = Tokenizer.from_pretrained("BAAI/bge-small-en-v1.5")
        self.embedder = TextEmbedding(mode_name="BAAI/bge-small-en-v1.5")

        self.max_tokens = 500
        self.overlap = 100
        self.stride = self.max_tokens - self.overlap


    def process_chunks(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Applies sliding window sub-chunking and embeds the text into vectors."""
        logger.info(f"Checking {len(chunks)} chunks for token limits...")
        processed_chunks = []

        for chunk in chunks:
            token_ids = self.tokenizer.encode(chunk["content"]).ids

            if len(token_ids) > self.max_tokens:

                for i in range(0, len(token_ids), self.stride):
                    slice_ids = token_ids[i : i+self.max_tokens]

                    new_chunk = copy.deepcopy(chunk)
                    new_chunk["content"] = self.tokenizer.decode(slice_ids)
                    processed_chunks.append(new_chunk)

            else:
                processed_chunks.append(chunk)

        logger.info(f"Generating vectors for {len(processed_chunks)} final chunks...")

        texts_to_embed = [c["content"] for c in processed_chunks]

        embeddings = list(self.embedder.embed(texts_to_embed))

        for i, chunk in enumerate(processed_chunks):
            chunk["vector"] = embeddings[i].tolist()

        logger.success(f"Successfully embedded {len(processed_chunks)} chunks")
        return processed_chunks