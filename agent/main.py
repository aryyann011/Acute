import typer
import sys
from loguru import logger
from agent.rag.scanner import RepositoryScanner
from agent.rag.ingest import CodeChunker

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
        raise typer.Exit(code=1)
    
    chunker = CodeChunker(valid_files)
    chunks = chunker.process_files()
    
    if chunks:
        logger.success("Printing a sample chunk to verify context enrichment:")
        print("\n" + "="*60)
        print(f"File: {chunks[0]['filepath']}")
        print(f"Function/Class Name: {chunks[0]['name']}")
        print(f"Lines: {chunks[0]['start_line']} to {chunks[0]['end_line']}")
        print("--- Enriched Content ---")
        print(chunks[0]['content'])
        print("="*60 + "\n")

@app.command()
def fix(bug: str):
    """
    Run the autonomous agent loop to fix a bug report.
    """
    logger.info(f"Initializing patch agent for bug: {bug}")

if __name__ == "__main__":
    app()