"""Node: lock_generator_agent."""

import logging

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from pydantic import BaseModel

from src.main_graph.config import get_services
from src.main_graph.subgraphs.discovery.state import DiscoveryState
from src.main_graph.subgraphs.discovery.tools.filesystem import read_file, write_file
from src.utils.llm import Model, get_llm

logger = logging.getLogger(__name__)
_llm = get_llm(Model.GPT_5_4_MINI)


class LockGenResult(BaseModel):
    success: bool
    attempts: int
    error: str | None


_SYSTEM_TEMPLATE = """\
You are generating a lock file for a Node.js project \
located at {repo_path} using {pm} in {image}.

Your goal is to successfully produce a valid lock file (package-lock.json, yarn.lock, \
    or pnpm-lock.yaml depending on the package manager).

Process:
- Use run_docker_command to install dependencies in the workspace.
- If the command fails, inspect the error output and decide the most appropriate fix.
-You may:
    - adjust Node image version
    - patch package.json when necessary
- Re-run after each fix (up to 6 attempts).

After each attempt:
- verify whether the expected lock file exists using read_file.

Stop when:
- the lock file is successfully generated and readable

Return:

success
lock_file_path
final_error (if any)
attempts
"""


async def lock_generator_agent(state: DiscoveryState, config: RunnableConfig) -> dict:
    svc = get_services(config)
    docker_tool = svc["docker_tool"]

    repo_path = state.get("repo_path")
    pm = state.get("detected_package_manager", "npm")
    image = state.get("docker_image", "node:lts-alpine")
    command = state.get("install_command", "npm install")

    agent = create_agent(
        model=_llm,
        tools=[docker_tool, read_file, write_file],
        response_format=LockGenResult,
    )

    try:
        result = await agent.ainvoke(
            {
                "messages": [
                    SystemMessage(
                        content=_SYSTEM_TEMPLATE.format(
                            repo_path=repo_path,
                            pm=pm,
                            image=image,
                            command=command,
                        )
                    ),
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
