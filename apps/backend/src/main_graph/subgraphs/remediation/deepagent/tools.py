from __future__ import annotations

from langchain_core.tools import tool
from langgraph.types import Command

from src.domain.ports.container_run_port import ContainerRunPort
from src.main_graph.subgraphs.discovery.dependency_graph import dependents_of
from src.main_graph.subgraphs.remediation.changelog import fetch_release_notes
from src.main_graph.subgraphs.remediation.verify import verify_working_copy
from src.main_graph.subgraphs.remediation.workspace import apply_bump
from src.models.remediation import MigrationPlan


def make_read_release_notes_tool(
    repo_path: str, container: ContainerRunPort, docker_image: str
):
    @tool
    async def read_release_notes(package_name: str) -> dict:
        """Fetch recent GitHub release notes for an npm package, resolved
        via its registry-declared repository URL. Use this to check for
        breaking changes between the installed version and a candidate
        upgrade before deciding whether a bump is safe, or whether code
        needs to change too."""
        return await fetch_release_notes(
            package_name, repo_path, container, docker_image
        )

    return read_release_notes


def make_dependents_of_tool(dependency_graph: dict):
    @tool
    def dependents_of_tool(package_name: str) -> list[str]:
        """Return every package in this project's dependency tree that
        depends on `package_name`, whether or not it has a flagged finding.
        Structural only - does not confirm a declared version range still
        holds after a bump; call `verify` for that."""
        return dependents_of(dependency_graph, package_name)

    return dependents_of_tool


def make_bump_dependency_tool(work_dir: str):
    @tool
    def bump_dependency(target_dep: str, to_range: str) -> dict:
        """Edit package.json to set target_dep's declared range to
        to_range. Returns {"applied": false} if target_dep isn't declared
        in dependencies/devDependencies."""
        return {"applied": apply_bump(work_dir, target_dep, to_range)}

    return bump_dependency


def make_verify_tool(
    work_dir: str,
    container: ContainerRunPort,
    docker_image: str,
    package_manager: str,
    default_targeted_deps: list[str],
):
    @tool
    async def verify(targeted_deps: list[str] | None = None) -> dict:
        """Install, build (if scripted), test (if scripted), and re-audit
        the working copy. Use this to self-correct as you iterate - it is
        a guide for your own next step, not the final verdict: a separate
        deterministic check re-verifies from a clean clone before anything
        ships."""
        result = await verify_working_copy(
            work_dir,
            container,
            docker_image,
            package_manager,
            targeted_deps or default_targeted_deps,
        )
        return result.model_dump()

    return verify


def make_commit_plan_tool():
    @tool
    def commit_plan(plan: MigrationPlan) -> Command:
        """Record the migration plan for this target. Call this FIRST,
        before dispatching any implementation work. The plan is
        persisted for review."""
        return Command(
            update={"migration_plans": {plan.target_dep: plan.model_dump()}}
        )

    return commit_plan
