from dataclasses import dataclass, field
from loguru import logger
from agent.tools.patcher import CodePatcher
from agent.tools.executor import TestRunner, TestResult

MAX_ATTEMPTS = 3
TEST_TIMEOUT_SECONDS = 60


@dataclass
class AttemptRecord:
    attempt: int
    applied: bool
    test_ran: bool
    test_passed: bool
    test_timed_out: bool
    failure_output: str
    first_error_line: str | None


@dataclass
class RunOutcome:
    fixed: bool
    reason: str
    attempts_used: int
    records: list[AttemptRecord] = field(default_factory=list)


def _same_patch(a, b) -> bool:
    """
    Two patches are identical when their (file, function, code) tuples match.
    The explanation field is intentionally ignored.
    """
    keys_a = [(edit.filepath, edit.target_function, edit.fixed_code) for edit in a.edits]
    keys_b = [(edit.filepath, edit.target_function, edit.fixed_code) for edit in b.edits]
    return keys_a == keys_b


def _first_error_line(output: str) -> str | None:
    """
    First line of the test tail that identifies the failure. Priority: exception lines
    (any 'Error' occurrence, e.g. 'E   KeyError: ...'), then pytest's FAILED summary,
    then the generic assert echo. Without priority the '>-prefixed source echo (which
    contains the word assert) would mask the real exception and false-trigger the
    stuck-gate on genuinely different failures.
    """
    for priority, markers in enumerate((("Error",), ("FAILED",), ("assert",))):
        for line in output.splitlines():
            stripped = line.strip()
            if stripped and any(marker in stripped for marker in markers):
                return (priority, stripped[:160])
    return None


def run_fix_loop(
    first_patch,
    bug_report: str,
    xml_context: str,
    coder,
    patcher: CodePatcher,
    tester: TestRunner | None,
    max_attempts: int = MAX_ATTEMPTS,
    run_tests: bool = True,
    test_timeout: int = TEST_TIMEOUT_SECONDS,
) -> RunOutcome:
    """
    The repair loop: apply patch, run tests, retry with feedback until fixed,
    a stop-gate triggers, or attempts are exhausted.

    Invariant: on every negative outcome (tests-passed excepted for the applied patch,
    and no-tests/applied-untested which leave the patch for human review), the repo
    is restored byte-identical to its state at loop start via the run-level snapshots.
    """
    snapshots: dict[str, str] = {}
    records: list[AttemptRecord] = []
    previous_patch = None
    previous_failure_output: str | None = None

    def snapshot_targets(patch) -> bool:
        for filepath in dict.fromkeys(edit.filepath for edit in patch.edits):
            if filepath not in snapshots:
                backup = patcher.snapshot_file(filepath)
                if backup is None:
                    logger.error(f"Snapshot failed for {filepath}. Aborting before any edit of this attempt.")
                    return False
                snapshots[filepath] = backup
        return True

    def rollback():
        for filepath, backup_path in snapshots.items():
            patcher.restore_file(filepath, backup_path)

    for attempt in range(1, max_attempts + 1):
        if len(records) >= 2 and records[-1].first_error_line and records[-1].first_error_line == records[-2].first_error_line:
            stuck_line = records[-1].first_error_line
            logger.error(f"Same failure repeated: '{stuck_line}'. No new signal for another attempt.")
            return RunOutcome(False, "stuck-on-same-error", attempt - 1, records)

        if attempt == 1:
            patch = first_patch
        else:
            logger.info(f"Attempt {attempt}/{max_attempts}: generating fix informed by the last failure.")
            patch = coder.generate_patch(
                bug_report,
                xml_context,
                previous_patch=previous_patch,
                failure_output=previous_failure_output,
            )
            if patch is None:
                logger.error("Gemini failed to produce a patch for this attempt.")
                return RunOutcome(False, "patch-generation-failed", attempt - 1, records)

            if _same_patch(patch, previous_patch):
                logger.error(f"Attempt {attempt}: model returned the identical patch again. Stopping (no progress).")
                return RunOutcome(False, "no-progress", attempt - 1, records)

        if len(records) >= 2 and records[-1].first_error_line and records[-1].first_error_line == records[-2].first_error_line:
            stuck_line = records[-1].first_error_line
            logger.error(f"Same failure repeated: '{stuck_line}'. No new signal for another attempt.")
            return RunOutcome(False, "stuck-on-same-error", attempt - 1, records)

        if not snapshot_targets(patch):
            rollback()
            return RunOutcome(False, "snapshot-failed", attempt - 1, records)

        reject_index = None
        for edit_index, edit in enumerate(patch.edits, start=1):
            logger.info(
                f"Attempt {attempt}: applying edit {edit_index}/{len(patch.edits)}"
                f" -> {edit.filepath} :: {edit.target_function}"
            )
            if not patcher.patch_file(edit.filepath, edit.target_function, edit.fixed_code, create_backup=False):
                reject_index = edit_index
                break

        if reject_index is not None:
            failed_edit = patch.edits[reject_index - 1]
            logger.error(
                f"Edit {reject_index} rejected ('{failed_edit.target_function}' in {failed_edit.filepath})."
                " Rolling back every change made in this run."
            )
            rollback()
            logger.error("Transaction rolled back. Nothing was left half-patched.")
            return RunOutcome(False, "apply-rejected", attempt, records)

        record = AttemptRecord(
            attempt=attempt, applied=True,
            test_ran=False, test_passed=False, test_timed_out=False,
            failure_output="", first_error_line=None,
        )
        records.append(record)

        if not run_tests:
            logger.success(f"Patch applied (--no-tests): {len(patch.edits)} edit(s) left in place for review.")
            return RunOutcome(False, "applied-untested", attempt, records)

        result: TestResult = tester.run(test_timeout)
        record.test_ran = result.ran
        record.test_passed = result.passed
        record.test_timed_out = result.timed_out

        if not result.ran:
            logger.warning(
                "No runnable test suite detected. The applied patch is left in place for manual review."
                " (Tip: try --dry-run first next time.)"
            )
            return RunOutcome(False, "no-tests", attempt, records)

        if result.passed:
            logger.success(f"FIXED on attempt {attempt}: {len(patch.edits)} edit(s) applied and all tests pass.")
            return RunOutcome(True, "tests-passed", attempt, records)

        record.failure_output = result.output
        record.first_error_line = _first_error_line(result.output)
        previous_failure_output = result.output
        previous_patch = patch

        logger.error(f"Attempt {attempt}: tests failed. Restoring repo to its original state before the next attempt.")
        rollback()

    return RunOutcome(False, "attempts-exhausted", max_attempts, records)
