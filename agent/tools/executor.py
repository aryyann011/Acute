import sys
import time
from dataclasses import dataclass
from pathlib import Path
import subprocess
import shutil
from loguru import logger

TAIL_LINE_LIMIT = 200

@dataclass
class TestResult:
    ran: bool
    passed: bool
    command: list[str] | None
    output: str
    elapsed_seconds: float
    timed_out: bool

class TestRunner:
    """
    Runs a target repository's test suite and returns a verdict.

    The verdict never lies: "no tests detected" (ran=False) is NOT the same as
    "all passed", and "timed out" is NOT the same as "failed".
    """

    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path)

    def detect_test_command(self) -> list[str] | None:
        """
        Figures out how to run this repo's tests from its own footprint, in priority order.
        Returns None when nothing recognizable exists.
        """
        pyproject = self.repo_path / "pyproject.toml"
        pytest_ini = self.repo_path / "pytest.ini"
        setup_py = self.repo_path / "setup.cfg"

        python_signal = pytest_ini.exists() or setup_py.exists()
        if pyproject.exists():
            python_signal = python_signal or "[tool.pytest" in pyproject.read_text(encoding="utf-8", errors="replace")
        tests_dir = self.repo_path / "tests"
        if tests_dir.exists() and any(tests_dir.glob("test_*.py")):
            python_signal = True

        if python_signal:
            return [sys.executable, "-m", "pytest", "-q"]

        package_json = self.repo_path / "package.json"
        if package_json.exists() and '"test"' in package_json.read_text(encoding="utf-8", errors="replace"):
            npm = shutil.which("npm")
            if npm:
                return [npm, "test"]

        return None

    def run(self, timeout_seconds: int = 60) -> TestResult:
        """
        Executes the detected test command from the repo directory with a hard timer,
        and returns a structured verdict with a truncated output tail.
        """
        command = self.detect_test_command()

        if command is None:
            logger.warning(f"No runnable test suite detected in {self.repo_path}.")
            return TestResult(ran=False, passed=False, command=None, output="", elapsed_seconds=0.0, timed_out=False)

        logger.info(f"Running tests: {' '.join(command)} (timeout {timeout_seconds}s)")
        started = time.monotonic()

        try:
            completed = subprocess.run(
                command,
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            elapsed = time.monotonic() - started
            logger.error(f"Test run exceeded {timeout_seconds}s and was killed.")
            return TestResult(ran=True, passed=False, command=command,
                              output="(tests timed out)", elapsed_seconds=elapsed, timed_out=True)
        except FileNotFoundError as e:
            logger.error(f"Test runner could not start: {e}")
            return TestResult(ran=False, passed=False, command=command,
                              output=f"(runner missing: {e})", elapsed_seconds=0.0, timed_out=False)

        elapsed = time.monotonic() - started
        raw_output = (completed.stdout or "") + "\n" + (completed.stderr or "")
        verdict_output = self._output_tail(raw_output)

        if completed.returncode == 0:
            logger.success(f"Tests PASSED in {elapsed:.1f}s")
        else:
            logger.error(f"Tests FAILED in {elapsed:.1f}s (exit code {completed.returncode})")

        return TestResult(
            ran=True,
            passed=(completed.returncode == 0),
            command=command,
            output=verdict_output,
            elapsed_seconds=elapsed,
            timed_out=False,
        )

    def _output_tail(self, raw_output: str) -> str:
        """
        Keeps the final lines of the test output (the error is always near the end)
        and trims it to a sane length for later use as retry-prompt feedback.
        """
        lines = raw_output.strip().splitlines()
        tail = lines[-TAIL_LINE_LIMIT:]
        return "\n".join(tail)
