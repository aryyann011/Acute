import json
from datetime import datetime
from pathlib import Path
from loguru import logger


def record_run(outcome, bug_report: str, no_tests: bool, runs_dir: str = "runs") -> Path | None:
    """
    Writes one JSON artifact per agent run into runs/ so Phase 3's benchmark
    can count pass rates and attempts-to-success from real evidence instead
    of recollection. Never raises: recording must not interrupt a finished run.
    """
    try:
        directory = Path(runs_dir)
        directory.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = directory / f"{timestamp}.json"

        artifact = {
            "timestamp": timestamp,
            "bug_report": bug_report,
            "no_tests_mode": no_tests,
            "fixed": outcome.fixed,
            "reason": outcome.reason,
            "attempts_used": outcome.attempts_used,
            "attempts": [
                {
                    "attempt": record.attempt,
                    "applied": record.applied,
                    "test_ran": record.test_ran,
                    "test_passed": record.test_passed,
                    "test_timed_out": record.test_timed_out,
                    "first_error_line": record.first_error_line,
                    "failure_output": record.failure_output[-2000:],
                }
                for record in outcome.records
            ],
        }
        path.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
        logger.info(f"Run recorded to {path}")
        return path
    except OSError as e:
        logger.warning(f"Could not record the run ({e}). The run result itself is unaffected.")
        return None
