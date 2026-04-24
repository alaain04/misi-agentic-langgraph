import subprocess
import sys


def dev():
    sys.exit(subprocess.call(["uvicorn", "src.main:app", "--reload"]))


def lint():
    sys.exit(subprocess.call(["ruff", "check", ".", "--fix"]))


def fmt():
    sys.exit(subprocess.call(["ruff", "format", "."]))


def test():
    sys.exit(subprocess.call(["pytest"]))
