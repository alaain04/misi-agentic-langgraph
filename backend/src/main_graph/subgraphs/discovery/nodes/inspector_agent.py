"""Node: inspector_agent — ReAct agent that reads Node.js manifests."""

import logging
import os

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from pydantic import BaseModel

from src.main_graph.subgraphs.discovery.state import DiscoveryState
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_5_4_MINI)


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


class InspectorResult(BaseModel):
    detected_package_manager: str  # "npm" | "yarn" | "pnpm"
    lock_file_missing: bool
    manifest_files: list[str]
    docker_image: str  # e.g. "node:22-alpine"
    install_command: str  # e.g. "npm install"


_SYSTEM_TEMPLATE = """\
You are inspecting a cloned Node.js repository at {repo_path}.

Steps:
1. Call list_dir("{repo_path}") to see root contents.
2. Call read_file("{repo_path}/package.json") to read the manifest.
3. Determine the package manager by lock file presence:
   pnpm-lock.yaml → pnpm, yarn.lock → yarn, package-lock.json → npm; default npm.
4. Set lock_file_missing=true if none of those three files exist in the root.
5. Read engines.node from package.json to pick the Docker image
   (e.g. "22" → "node:22-alpine"); default "node:lts-alpine".
6. Set install_command to the appropriate command
   (npm install / yarn install / pnpm install).
7. Return structured output.

If package.json is missing, return:
  detected_package_manager="npm", lock_file_missing=true, manifest_files=[],
  docker_image="node:lts-alpine", install_command="npm install".
"""


async def inspector_agent(state: DiscoveryState) -> dict:
    repo_path = state.get("repo_path", "")
    if not repo_path:
        return {"discovery_error": "No repo_path available for inspection"}

    try:
        _agent = create_agent(
            model=_llm,
            tools=[list_dir, read_file],
            response_format=InspectorResult,
        )

        result = await _agent.ainvoke(
            {
                "messages": [
                    SystemMessage(content=_SYSTEM_TEMPLATE.format(repo_path=repo_path)),
                    HumanMessage(content="Inspect the repository now."),
                ]
            },
            config={"recursion_limit": 15},
        )
        output: InspectorResult = result["structured_response"]
    except Exception as exc:
        logger.exception("inspector_agent: failed")
        return {"discovery_error": f"Inspector agent failed: {exc}"}

    return {
        "detected_package_manager": output.detected_package_manager,
        "lock_file_missing": output.lock_file_missing,
        "manifest_files": output.manifest_files,
        "docker_image": output.docker_image,
        "install_command": output.install_command,
    }
