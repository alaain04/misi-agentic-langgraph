"""Node: lock_generator_agent — ReAct agent that installs deps and generates a lock file."""

import asyncio
import json
import logging
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from langchain.agents import create_agent
from pydantic import BaseModel

from src.main_graph.subgraphs.discovery.nodes.inspector_agent import read_file
from src.main_graph.subgraphs.discovery.state import DiscoveryState
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_5_4_MINI)

_DOCKER_TIMEOUT = 300


@tool
async def run_docker_command(image: str, command: str, workspace: str) -> str:
    """Run a shell command in a Docker container with the workspace mounted.

    Returns JSON with keys: returncode (int), stdout (str), stderr (str).
    """
    proc = await asyncio.create_subprocess_exec(
        "docker", "run", "--rm",
        "-v", f"{workspace}:/workspace",
        image, "sh", "-c", f"cd /workspace && {command}",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=_DOCKER_TIMEOUT)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return json.dumps({"returncode": -1, "stdout": "", "stderr": f"timed out after {_DOCKER_TIMEOUT}s"})
    return json.dumps({
        "returncode": proc.returncode,
        "stdout": stdout_b.decode(errors="replace"),
        "stderr": stderr_b.decode(errors="replace")[:3000],
    })


def _make_write_file_tool(repo_path: str):
    workspace = Path(repo_path).resolve()

    @tool
    def write_file(relative_path: str, content: str) -> str:
        """Write content to a file within the workspace. Path must be relative to workspace root."""
        target = (workspace / relative_path).resolve()
        if not str(target).startswith(str(workspace)):
            return f"Error: path '{relative_path}' is outside the workspace"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return f"Wrote {len(content)} bytes to {target}"

    return write_file


class LockGenResult(BaseModel):
    success: bool
    attempts: int
    error: str | None


_SYSTEM_TEMPLATE = """\
You are generating a lock file for a Node.js project.

Workspace: {repo_path}
Package manager: {pm}
Docker image: {image}
Initial install command: {command}

Expected lock file:
- npm  -> package-lock.json
- yarn -> yarn.lock
- pnpm -> pnpm-lock.yaml

Steps:
1. Run run_docker_command(image="{image}", command="{command}", workspace="{repo_path}").
2. If it fails (returncode != 0), read the stderr carefully.
3. Apply one fix per retry:
   - npm peer conflict        -> append --legacy-peer-deps to the command
   - npm engine mismatch      -> switch to a different node image tag (e.g. node:18-alpine)
   - optional dep failure     -> append --ignore-optional
   - version range conflict   -> read_file("{repo_path}/package.json"), patch the conflicting range with write_file, then retry
4. Repeat up to 6 total attempts.
5. After each run, verify the lock file exists by calling read_file with its expected path.
6. Report success=true if the lock file is readable, otherwise success=false with the last error.
"""


async def lock_generator_agent(state: DiscoveryState) -> dict:
    repo_path = state.get("repo_path", "")
    pm = state.get("detected_package_manager", "npm")
    image = state.get("docker_image", "node:lts-alpine")
    command = state.get("install_command", "npm install")

    write_file_tool = _make_write_file_tool(repo_path)
    agent = create_agent(
        model=_llm,
        tools=[run_docker_command, read_file, write_file_tool],
        response_format=LockGenResult,
    )

    try:
        result = await agent.ainvoke(
            {
                "messages": [
                    SystemMessage(content=_SYSTEM_TEMPLATE.format(
                        repo_path=repo_path, pm=pm, image=image, command=command,
                    )),
                    HumanMessage(content="Generate the lock file now."),
                ]
            },
            config={"recursion_limit": 25},
        )
        output: LockGenResult = result["structured_response"]
    except Exception as exc:
        logger.exception("lock_generator_agent: failed")
        return {
            "lock_generation_attempts": 0,
            "lock_generation_error": f"Lock generator agent failed: {exc}",
        }

    return {
        "lock_generation_attempts": output.attempts,
        "lock_generation_error": output.error if not output.success else None,
    }
