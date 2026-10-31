"""
Phase 1 verification suite - run with:  uv run python verify_phase1.py

Proves the patch engine's safety guarantees WITHOUT calling Gemini:
  1. surgical patch (target function changes, nothing else does)
  2. indentation gate (Gemini echo with extra indent lands at the correct depth)
  3. broken code is rejected before write (file byte-identical)
  4. garbage code is rejected before write (file byte-identical)
  5. transaction rollback (a failing batch restores both files byte-identical)
  6. dry-run preview (clean verdict, zero disk writes)
"""
import tempfile
import shutil
import ast
from pathlib import Path
from agent.tools.patcher import CodePatcher

PASSED = []

DEFAULT_FILE = (
    "APPROVED = {}\n"
    "\n"
    "class Svc:\n"
    "    def verify(self, token: str):\n"
    "        if not token:\n"
    "            return False\n"
    "        return True\n"
)


def fresh_file(tmp: Path, name: str, text: str) -> Path:
    f = tmp / name
    f.write_text(text, newline="")
    return f


def case_surgical_patch(p: CodePatcher, tmp: Path):
    f = fresh_file(tmp, "case1.py", DEFAULT_FILE)
    original = f.read_bytes()
    ok = p.patch_file(
        str(f), "verify",
        "def verify(self, token: str):\n"
        "    if not token:\n"
        "        return False\n"
        "    return token.count('.') == 2\n",
        create_backup=False, dry_run=True,
    )
    assert ok, "surgical patch should be judged clean (dry-run)"
    real_ok = p.patch_file(str(f), "verify",
        "def verify(self, token: str):\n"
        "    if not token:\n"
        "        return False\n"
        "    return token.count('.') == 2\n",
        create_backup=False)
    assert real_ok, "surgical patch should apply"
    ast.parse(f.read_bytes().decode())
    assert f.read_bytes() != original, "file should have changed"
    assert f.read_bytes().decode().startswith("APPROVED = {}"), "non-target content must survive"
    PASSED.append("1. surgical patch (target only)")


def case_indentation_gate(p: CodePatcher, tmp: Path):
    f = fresh_file(tmp, "case2.py", DEFAULT_FILE)
    gemini_echo = (
        "        def verify(self, token: str):\n"       # 8-space extra indent (double-indent trap)
        "            if not token:\n"
        "                return False\n"
        "            return token.count('.') == 2\n"
    )
    ok = p.patch_file(str(f), "verify", gemini_echo, create_backup=False)
    assert ok, "over-indented echo should be normalized and applied"
    text = f.read_bytes().decode()
    ast.parse(text)
    lines = text.splitlines()
    assert lines[2] == "class Svc:", "class line must be untouched"
    assert lines[3] == "    def verify(self, token: str):", f"def must sit at file's 4-space depth, got {lines[3]!r}"
    assert lines[4] == "        if not token:", f"body must be 4 deeper (8 spaces), got {lines[4]!r}"
    PASSED.append("2. indentation gate (double-indent echo)")


def case_broken_code_rejected(p: CodePatcher, tmp: Path):
    f = fresh_file(tmp, "case3.py", DEFAULT_FILE)
    original = f.read_bytes()
    broken = "        def verify(self, token: str):\n        return False\n"   # flat body => invalid
    ok = p.patch_file(str(f), "verify", broken, create_backup=False)
    assert not ok, "flat body must be rejected by the syntax gate"
    assert f.read_bytes() == original, "file must be byte-identical after rejection"
    PASSED.append("3. broken code rejected before write")


def case_garbage_rejected(p: CodePatcher, tmp: Path):
    f = fresh_file(tmp, "case4.py", DEFAULT_FILE)
    original = f.read_bytes()
    ok = p.patch_file(str(f), "verify", "def alpha(:\n  return ", create_backup=False)
    assert not ok, "garbage must be rejected by the syntax gate"
    assert f.read_bytes() == original, "file must be byte-identical after rejection"
    PASSED.append("4. garbage code rejected before write")


def case_transaction_rollback(p: CodePatcher, tmp: Path):
    fa = fresh_file(tmp, "case5a.py", "def alpha():\n    return 1\n")
    fb = fresh_file(tmp, "case5b.py", "def beta():\n    return 2\n")
    orig_a, orig_b = fa.read_bytes(), fb.read_bytes()

    snapshots = {str(fa): p.snapshot_file(str(fa)), str(fb): p.snapshot_file(str(fb))}
    ok1 = p.patch_file(str(fa), "alpha", "def alpha():\n    return 10\n", create_backup=False)
    ok2 = p.patch_file(str(fb), "gamma_missing", "def gamma():\n    return 3\n", create_backup=False)
    assert ok1, "first edit should apply"
    assert not ok2, "second edit (missing function) should fail"

    for filepath, backup_path in snapshots.items():
        assert p.restore_file(filepath, backup_path), "rollback must not crash"
    assert fa.read_bytes() == orig_a, "file A (modified) must return byte-identical"
    assert fb.read_bytes() == orig_b, "file B (untouched) must stay byte-identical"
    ast.parse(fa.read_bytes().decode())
    PASSED.append("5. transaction rollback (byte-identical)")


def case_dry_run_touches_nothing(p: CodePatcher, tmp: Path):
    f = fresh_file(tmp, "case6.py", DEFAULT_FILE)
    original = f.read_bytes()
    ok = p.patch_file(str(f), "verify", "def verify(self, token: str):\n    return False\n",
                      create_backup=False, dry_run=True)
    assert ok, "dry-run should preview cleanly"
    assert f.read_bytes() == original, "dry-run must not write a single byte"
    PASSED.append("6. dry-run writes nothing")


def main():
    p = CodePatcher()
    tmp = Path(tempfile.mkdtemp())
    try:
        case_surgical_patch(p, tmp)
        case_indentation_gate(p, tmp)
        case_broken_code_rejected(p, tmp)
        case_garbage_rejected(p, tmp)
        case_transaction_rollback(p, tmp)
        case_dry_run_touches_nothing(p, tmp)
    finally:
        shutil.rmtree(tmp)

    print("\n" + "=" * 60)
    for item in PASSED:
        print(f"  PASS  {item}")
    print("=" * 60)
    print(f"  {len(PASSED)}/6 guarantees verified.\n")


if __name__ == "__main__":
    main()
