import typer
import sys
from loguru import logger
from agent.rag.scanner import RepositoryScanner
from agent.rag.ingest import CodeChunker
from agent.rag.embedder import VectorEmbedder
from agent.rag.vector_store import QdrantDB

app = typer.Typer()

@app.callback()
def Colourization():
    """Acute : Autonomous codebase patching agent"""
    logger.remove()

    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> |<level>{level: <8}</level> |<cyan>{message}</cyan>",
        level="INFO"
    )

    logger.add("logs/agent_run.log", rotation="5 MB", level="DEBUG")

@app.command()
def index(path: str = "."):
    """
    Index the codebase into the local vector database.
    """
    logger.info(f"Starting ingestion process for directory: {path}")
    logger.debug(f"DEBUG: Initializing file scanner at absolute path for {path}")
    
    scanner = RepositoryScanner(path)
    valid_files = scanner.get_tracked_files()
    
    if not valid_files:
        logger.warning("No files found to process. Aborting.")
    chunker = CodeChunker()
    chunks = chunker.process_files(valid_files)
    
    if not chunks:
        logger.warning("No chunks generated. Aborting.")
        raise typer.Exit(code=1)

    embedder = VectorEmbedder()
    embedded_chunks = embedder.process_chunks(chunks)
    
    if embedded_chunks:
        logger.success("Printing a sample embedded chunk:")
        print("\n" + "="*60)
        print(f"File: {embedded_chunks[0]['filepath']}")
        print(f"Function/Class Name: {embedded_chunks[0]['name']}")
        print(f"Vector Dimension (Size): {len(embedded_chunks[0]['vector'])}")
        print(f"Vector Sample (first 5 floats): {embedded_chunks[0]['vector'][:5]}")
        print("="*60 + "\n")

    logger.info("Connecting to local Qdrant Vector DB...")
    db = QdrantDB()
    db.insert_chunks(embedded_chunks)
    
    logger.success(f"Phase 2 Complete. {len(embedded_chunks)} chunks securely persisted to disk.")

@app.command()
def fix(bug: str):
    """
    Run the autonomous agent loop to fix a bug report.
    """
    if str == "":
        logger.error(f"nothing to fix the prompt is empty")
        return 

    
    logger.info(f"Initializing patch agent for bug: {bug}")

if __name__ == "__main__":
    app()