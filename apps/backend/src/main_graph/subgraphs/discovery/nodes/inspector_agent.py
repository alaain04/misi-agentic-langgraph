"""Node: inspector_agent — ReAct agent that reads Node.js manifests."""

import logging

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from src.main_graph.subgraphs.discovery.state import DiscoveryState
from src.main_graph.subgraphs.discovery.tools.filesystem import list_dir, read_file
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_5_4)


class InspectorResult(BaseModel):
    detected_package_manager: str  # "npm" | "yarn" | "pnpm"
    package_manager_version: str  # from packageManager field, e.g. "9.15.0"; "latest" if absent
    lock_file_missing: bool
    manifest_files: list[str]
    docker_image: str  # e.g. "node:22-alpine"
    install_command: str  # e.g. "npm install"


_SYSTEM_TEMPLATE = """\
You are analyzing a Node.js repository at {repo_path}, \
using the available inspection tools.

Infer:
detected_package_manager, package_manager_version, lock_file_missing, \
manifest_files, docker_image, install_command

Rules:
- Detect package manager from lock files:
    pnpm-lock.yaml → pnpm; yarn.lock → yarn; package-lock.json → npm; default: npm
- lock_file_missing=true if no known lock file exists.
- Read package.json.packageManager (e.g. "pnpm@9.15.0") to extract package_manager_version.
  Strip any hash suffix (e.g. "+sha256.abc"). Default: "latest".
- Select docker_image to satisfy BOTH the project's engines.node AND the package manager's \
Node requirement:
    pnpm v11+ requires Node >=22; otherwise follow engines.node.
    Take the higher of the two requirements.
    Examples:
      engines.node=">=20", pnpm@11 → "node:22-alpine"
      engines.node=">=22", pnpm@9  → "node:22-alpine"
      engines.node=">=20", npm     → "node:20-alpine"
    Fallback: "node:lts-alpine"
- Install commands:
    npm → npm install; yarn → yarn install; pnpm → pnpm install

If package.json is missing or unreadable, return defaults.
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
        "package_manager_version": output.package_manager_version,
        "lock_file_missing": output.lock_file_missing,
        "manifest_files": output.manifest_files,
        "docker_image": output.docker_image,
        "install_command": output.install_command,
    }
