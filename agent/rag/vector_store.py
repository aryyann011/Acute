import uuid
from typing import List, Dict, Any
from loguru import logger
from qdrant_client import QdrantClient
from qdrant_client.http import models

class QdrantDB:
    def __init__(self, collection_name: str = "codebase"):
        self.collection_name = collection_name

        logger.debug("Initialising local Qdratn database connection...")
        self.client = QdrantClient(path=".agent_db")

    def recreate_collection(self):
        """The Nuke&Rebuild phase.
        Drops the collection if it exists and creates a fresh one.
        """

        self.client.recreate_collection(
            collection_name=self.collection_name,
            vectors_config=models.VectorParams(
                size=384,
                distance=models.Distance.COSINE
            )
        )

    def insert_chunks(self, chunks:List[Dict[str, Any]]):
        """
        Takes the embedded chunks, formats them into Qdrant Points, and insets them
        """

        if not chunks:
            logger.warning("no chunks provided to insert")
            return

        self.recreate_collection()

        points = []

        for chunk in chunks:
            point_id = str(uuid.uuid4())

            vector = chunk.pop("vector")

            point = models.PointStruct(
                id=point_id,
                vector=vector,
                payload=chunk
            )

            points.append(point)

            logger.info(f"uploading {len(points)} points to Qdrant...")

            self.client.upsert(
                collection_name=self.collection_name,
                points=points
            )
            logger.success(f"Successfully indexed {len(points)} chunks into the database")

    def search(self, query_vector:List[float], limit: int=5) -> List[Dict[str, Any]]:
        """Takes vectorized bug report and returns the closest code chunks"""
        logger.info(f"Searching database for top {limit} closest matches...")
        search_results = self.client.search(
            collection_name=self.collection_name,
            query_vector=query_vector,
            limit=limit
        )

        results = []
        for hit in search_results:
            results.append(hit.payload)

        return results