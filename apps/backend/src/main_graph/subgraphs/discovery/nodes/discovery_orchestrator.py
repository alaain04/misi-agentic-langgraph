"""Node: discovery_orchestrator — single ReAct agent for all discovery work."""

import logging
import os

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

from src.main_graph.config import get_services
from src.main_graph.subgraphs.discovery.models import SbomEntry
from src.main_graph.subgraphs.discovery.state import DiscoveryState
from src.main_graph.subgraphs.discovery.tools.filesystem import list_dir, read_file
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)

_llm = get_llm(Model.GPT_5_4)

_SYSTEM = """\
You are a Node.js dependency discovery agent. Your goal is to clone a repository, \
inspect it, generate a valid CycloneDX SBOM, and return a structured result.

You have these tools:
- run_docker_command(image, volume, command): run a shell command in a Docker container
- list_dir(path): list files at a local path on the host
- read_file(path): read a local file on the host

## Step 1 — Clone

Run:
  run_docker_command(
    image="alpine/git",
    volume="{tmp_dir}:/workspace",
    command="git clone --depth=1 --single-branch {repo_url} /workspace"
  )

If returncode != 0, set discovery_error to the stderr and stop — return the result now.
repo_path is always "{tmp_dir}".

## Step 2 — Inspect

Use list_dir and read_file on "{tmp_dir}" to determine:
- detected_package_manager: check which lock file exists:
    pnpm-lock.yaml → "pnpm", yarn.lock → "yarn", package-lock.json → "npm"; default: "npm"
- package_manager_version: read package.json "packageManager" field (e.g. "pnpm@9.15.0" → "9.15.0");
    strip any hash suffix; default: "latest"
- manifest_files: list of ["package.json", "pnpm-lock.yaml", "yarn.lock", "package-lock.json"]
    that actually exist
- docker_image: select to satisfy BOTH engines.node AND package manager Node requirement:
    pnpm v11+ requires Node >=22; otherwise follow engines.node; take the higher.
    Examples: engines.node=">=20" + pnpm@11 → "node:22-alpine"
              engines.node=">=22" + npm → "node:22-alpine"
              engines.node=">=20" + npm → "node:20-alpine"
    Fallback if unreadable: "node:lts-alpine"

## Step 3 — Generate SBOM

volume for all SBOM commands: "{tmp_dir}:/workspace"

### If a lock file IS present — try SBOM directly (no install needed):

  pnpm-lock.yaml:
    run_docker_command(image=docker_image, volume=...,
      command="cd /workspace && NO_UPDATE_NOTIFIER=1 npm install -g pnpm@{{pm_version}} && pnpm sbom --sbom-format=cyclonedx --package-lock-only")
    (replace {{pm_version}} with the version you detected in Step 2, e.g. "9.15.0" or "latest")

  package-lock.json:
    run_docker_command(image=docker_image, volume=...,
      command="cd /workspace && NO_UPDATE_NOTIFIER=1 npm sbom --sbom-format=cyclonedx --package-lock-only")

  yarn.lock:
    run_docker_command(image=docker_image, volume=...,
      command="cd /workspace && NO_UPDATE_NOTIFIER=1 npm install --package-lock-only --ignore-scripts && NO_UPDATE_NOTIFIER=1 npm sbom --sbom-format=cyclonedx --package-lock-only")

### If NO lock file — generate one first, then SBOM:

  pnpm:
    run_docker_command(image=docker_image, volume=...,
      command="cd /workspace && NO_UPDATE_NOTIFIER=1 npm install -g pnpm@{{pm_version}} && pnpm install")
  npm/yarn:
    run_docker_command(image=docker_image, volume=...,
      command="cd /workspace && NO_UPDATE_NOTIFIER=1 npm install --ignore-scripts")

  Verify the lock file was created with read_file. Then run the SBOM command for the detected pm.

## Step 4 — Retry strategy (max 8 total SBOM attempts)

When a SBOM or install command fails, read the stderr and adapt:
- "ERESOLVE" or "peer" conflict → append --legacy-peer-deps to the npm command; if that also fails, use --force
- "pnpm: command not found" or pnpm exits non-zero → fall back to the npm command for the same lock
- Node version error ("requires Node") → switch docker_image to "node:22-alpine" then "node:20-alpine"
- Any other failure → try --legacy-peer-deps first, then --force as last resort

Each retry is a new run_docker_command call. Count attempts. After 8 failures stop and set sbom_error.

## Step 5 — SBOM output

On success, stdout contains the raw CycloneDX JSON. Parse it as sbom_cyclonedx.
On total failure, set sbom_cyclonedx={{}} and sbom_error to the last error message.
"""


class OrchestratorResult(BaseModel):
    repo_path: str
    detected_package_manager: str
    package_manager_version: str
    manifest_files: list[str]
    docker_image: str
    sbom_cyclonedx: dict
    sbom_error: str | None = None
    discovery_error: str | None = None


async def discovery_orchestrator(state: DiscoveryState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    docker_tool = svc["docker_tool"]
    sbom_dao = svc["sbom_dao"]

    job_id = state["job_id"]
    repo_url = state["repo_url"]
    tmp_dir = os.path.abspath(f"tmp/debug_job_{job_id}")
    os.makedirs(tmp_dir, exist_ok=True)

    agent = create_agent(
        model=_llm,
        tools=[docker_tool, list_dir, read_file],
        response_format=OrchestratorResult,
    )

    try:
        agent_result = await agent.ainvoke(
            {
                "messages": [
                    SystemMessage(content=_SYSTEM.format(tmp_dir=tmp_dir, repo_url=repo_url)),
                    HumanMessage(content="Run the full discovery now."),
                ]
            },
            config={"recursion_limit": 40},
        )
        output: OrchestratorResult = agent_result["structured_response"]
    except Exception as exc:
        logger.exception("discovery_orchestrator: agent failed")
        entry = SbomEntry(repo_url=repo_url, scan_error=str(exc))
        result_id = await sbom_dao.save(entry)
        return {
            "repo_path": tmp_dir,
            "manifest_files": [],
            "detected_package_manager": "npm",
            "package_manager_version": "latest",
            "docker_image": "node:lts-alpine",
            "sbom_cyclonedx": {},
            "sbom_result_id": result_id,
            "discovery_error": f"Discovery agent failed: {exc}",
        }

    entry = SbomEntry(
        repo_url=repo_url,
        sbom_cyclonedx=output.sbom_cyclonedx,
        scan_error=output.sbom_error,
    )
    result_id = await sbom_dao.save(entry)
    logger.info(
        "discovery_orchestrator: done pm=%s sbom_error=%s discovery_error=%s",
        output.detected_package_manager,
        output.sbom_error,
        output.discovery_error,
    )

    out: dict = {
        "repo_path": output.repo_path,
        "manifest_files": output.manifest_files,
        "detected_package_manager": output.detected_package_manager,
        "package_manager_version": output.package_manager_version,
        "docker_image": output.docker_image,
        "sbom_cyclonedx": output.sbom_cyclonedx,
        "sbom_result_id": result_id,
    }
    if output.sbom_error:
        out["sbom_error"] = output.sbom_error
    if output.discovery_error:
        out["discovery_error"] = output.discovery_error
    return out
