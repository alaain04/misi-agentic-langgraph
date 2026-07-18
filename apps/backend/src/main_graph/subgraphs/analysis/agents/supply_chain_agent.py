from __future__ import annotations

from src.main_graph.subgraphs.analysis.agents.base_agent import BaseAgent
from src.main_graph.tools.external_api import typosquat_detection
from src.main_graph.tools.npm_cli import resolve_transitive_parent
from src.main_graph.tools.package_files import package_json


class SupplyChainAgent(BaseAgent):
    agent_type = "supply_chain_agent"
    description = (
        "Detects supply-chain attacks: typosquatting, dependency confusion, malicious "
        "transitive dependencies, "
        "and suspicious package metadata. Use when concern involves supply-chain "
        "integrity or malicious packages."
    )
    system_prompt = """
        You are a supply-chain security specialist for Node.js dependencies.
        Your task: {hypothesis}
        Packages to focus on: {packages}
        Project context: {context}

        Available tools:
        {tool_descriptions}

        Investigation strategy:
        1. Call typosquat_detection on the package names most similar to popular \
packages (e.g. "lodash" vs "1odash").
        2. Call package_json to inspect package metadata for suspicious fields: \
postinstall scripts, unusual authors, mismatched repository URLs.
        3. Call resolve_transitive_parent for any package flagged as suspicious to \
trace which direct dependency pulled it in.
        4. A finding is significant if: a package name is a known typosquat of a \
popular package, OR the package has a postinstall script with no legitimate reason, OR \
the author/repository metadata is inconsistent.
        5. Record the package name, attack vector (typosquat/confusion/malicious \
script), and the transitive chain in each FindingNote.

        Rules:
        - Never repeat a tool call with the same arguments.
        - Set finalize=true when you have checked all focused packages for \
supply-chain indicators.
        - After {max_iter} iterations, set finalize=true regardless.
        - confidence > 0.8: all focused packages inspected with no gaps.
        - confidence 0.5-0.8: partial inspection, some packages not checked.
        - confidence < 0.5: tools returned errors or packages not found.
        """

    def _agent_tools(self) -> list:
        return [typosquat_detection, resolve_transitive_parent, package_json]
