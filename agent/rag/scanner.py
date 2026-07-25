from pathlib import Path
from git import Repo, InvalidGitRepositoryError
from loguru import logger

class RepositoryScanner:

    ALLOWED_EXTENSIONS = {".py", ".js", ".ts"} 
    IGNORED_FILES = {"package-lock.json"}

    
    def __init__(self, repo_path: str):
        
        self.repo_path = Path(repo_path).resolve()

    def get_tracked_files(self) -> list[Path]:
        try:
            repo = Repo(self.repo_path)
            logger.debug(f"Connected to Git at: {self.repo_path}")
        except InvalidGitRepositoryError:
            logger.error("Not a valid Git repository.")
            return []

        valid_files = []
        
        raw_files = repo.git.ls_files().split('\n')
        
        for file_path in raw_files:
            
            if not file_path:
                continue
                
            path_obj = Path(file_path)
            
            if path_obj.suffix not in self.ALLOWED_EXTENSIONS:
                continue
                
            if path_obj.name in self.IGNORED_FILES:
                continue
                
            full_path = self.repo_path / path_obj
            
            valid_files.append(full_path)
            
        logger.info(f"Found {len(valid_files)} valid files.")
        
        return valid_files