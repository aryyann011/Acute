import typer
import sys
from loguru import logger
from agent.rag.scanner import RepositoryScanner

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
    scanner = RepositoryScanner(path)
    valid_files = scanner.get_tracked_files()
    logger.info(f"Starting ingestion process for directory: {path}")
    logger.debug(f"DEBUG: Initializing file scanner at absolute path for {path}")

@app.command()
def fix(bug: str):
    """
    Run the autonomous agent loop to fix a bug report.
    """
    logger.info(f"Initializing patch agent for bug: {bug}")

if __name__ == "__main__":
    app()