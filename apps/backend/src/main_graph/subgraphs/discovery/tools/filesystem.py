import os
from pathlib import Path

from langchain_core.tools import tool


@tool
def list_dir(path: str) -> str:
    """List files and directories at the given absolute path."""
    try:
        return "\n".join(os.listdir(path))
    except FileNotFoundError:
        return f"Directory not found: {path}"
    except Exception as exc:
        return f"Error listing directory: {exc}"


@tool
def read_file(path: str) -> str:
    """Read the contents of a file at the given absolute path."""
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return f"File not found: {path}"
    except Exception as exc:
        return f"Error reading file: {exc}"


@tool
def write_file(repo_path: str, content: str) -> str:
    """Write content to a file within the workspace.

    Path must be relative to workspace root.
    """
    workspace = Path(repo_path).resolve()
    target = (workspace / repo_path).resolve()

    if not str(target).startswith(str(workspace)):
        return f"Error: path '{repo_path}' is outside the workspace"

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)

    return f"Wrote {len(content)} bytes to {target}"
