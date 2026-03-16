#!/usr/bin/env python3
"""Validate all Python files compile on Python 3.9 (macOS system Python)."""

import os
import py_compile
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Patterns that crash on Python 3.9 at import time
BAD_PATTERNS = re.compile(r'\b(list|dict|tuple|set)\[')


def find_python_files():
    """Find all .py files in skill directories."""
    files = []
    for skill in ("worklog-logging", "self-improve", "worklog-analysis"):
        skill_dir = os.path.join(REPO_ROOT, skill)
        for root, _, filenames in os.walk(skill_dir):
            for f in filenames:
                if f.endswith(".py"):
                    files.append(os.path.join(root, f))
    return files


def test_compile():
    """All Python files must compile without error."""
    failures = []
    for path in find_python_files():
        try:
            py_compile.compile(path, doraise=True)
        except py_compile.PyCompileError as e:
            failures.append((path, str(e)))
    return failures


def test_no_310_types():
    """No Python 3.10+ type annotations in function signatures."""
    failures = []
    for path in find_python_files():
        with open(path) as f:
            for i, line in enumerate(f, 1):
                # Only check function signatures (def lines, param annotations, return types)
                if line.strip().startswith("def ") or "->" in line:
                    matches = BAD_PATTERNS.findall(line)
                    if matches:
                        failures.append((path, i, line.strip(), matches))
    return failures


def main():
    print("Python %s" % sys.version)
    print()

    py_files = find_python_files()
    print("Found %d Python files" % len(py_files))
    for f in py_files:
        print("  %s" % os.path.relpath(f, REPO_ROOT))
    print()

    # Test 1: Compilation
    compile_failures = test_compile()
    if compile_failures:
        print("FAIL: Compilation errors:")
        for path, err in compile_failures:
            print("  %s: %s" % (os.path.relpath(path, REPO_ROOT), err))
    else:
        print("PASS: All files compile")

    # Test 2: No 3.10+ types
    type_failures = test_no_310_types()
    if type_failures:
        print("FAIL: Python 3.10+ type annotations found:")
        for path, line_num, line, matches in type_failures:
            print("  %s:%d: %s (found: %s)" % (
                os.path.relpath(path, REPO_ROOT), line_num, line, matches))
    else:
        print("PASS: No Python 3.10+ type annotations in signatures")

    print()
    if compile_failures or type_failures:
        print("FAILED")
        sys.exit(1)
    else:
        print("ALL PASSED")


if __name__ == "__main__":
    main()
