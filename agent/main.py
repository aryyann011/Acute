import typer

app = typer.Typer()

@app.command()
def index(path: str = "."):
    """
    Index the codebase into the local vector database.
    """
    typer.echo(f"Indexing target directory: {path}")

@app.command()
def fix(bug: str):
    """
    Run the autonomous agent loop to fix a bug report.
    """
    typer.echo(f"Initializing patch agent for bug: {bug}")

if __name__ == "__main__":
    app()