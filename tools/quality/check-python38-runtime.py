#!/usr/bin/env python3
"""Compile Python shipped to Ubuntu 20.04 with a Python 3.8 interpreter."""

import ast
import pathlib
import re
import sys


ROOT = pathlib.Path(__file__).resolve().parents[2]
SHELL_FILES = [
    ROOT / ".github/scripts/remote-deploy.sh",
    ROOT / "deploy/installer/install.sh",
    ROOT / "deploy/installer/sourcelens/install.sh",
    ROOT / "src/agent/packaging/install/install.sh",
    ROOT / "src/agent/scripts/package.sh",
]
PYTHON_FILES = [
    ROOT / "deploy/installer/apply-runtime-config.py",
    ROOT / "deploy/installer/configure-sl-queue-monitor.py",
    ROOT / "tools/config/sync_env.py",
]
PYTHON_FILES.extend(sorted((ROOT / "deploy/installer/sourcelens").glob("*.py")))
HEREDOC = re.compile(r"<<-?'(?P<delimiter>PY(?:THON)?)'")


def validate_source(source, filename):
    if sys.version_info[:2] != (3, 8):
        ast.parse(source, filename=filename, feature_version=8)
    compile(source, filename, "exec")


def compile_quoted_python_heredocs(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    index = 0
    while index < len(lines):
        match = HEREDOC.search(lines[index])
        if match is None:
            index += 1
            continue
        delimiter = match.group("delimiter")
        start = index + 2
        index += 1
        body = []
        while index < len(lines) and lines[index].strip() != delimiter:
            body.append(lines[index])
            index += 1
        if index == len(lines):
            raise SyntaxError("unterminated {} heredoc in {}".format(delimiter, path))
        filename = "{}:{}".format(path, start)
        validate_source("\n".join(body) + "\n", filename)
        index += 1


for script in SHELL_FILES:
    compile_quoted_python_heredocs(script)

for source in PYTHON_FILES:
    validate_source(source.read_text(encoding="utf-8"), str(source))

print("Ubuntu 20.04 Python 3.8 runtime compatibility checks passed.")
