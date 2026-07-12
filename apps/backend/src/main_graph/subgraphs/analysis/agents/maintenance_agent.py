from __future__ import annotations

from src.main_graph.subgraphs.analysis.agents.base_agent import BaseAgent
from src.main_graph.tools.external_api import high_risk_packages, package_reputation, unmaintained_packages


class MaintenanceAgent(BaseAgent):
    agent_type = "maintenance_agent"
    description = (
        "Assesses package health and abandonment risk by checking maintenance status, download trends, "
        "and known high-risk packages. Use when concern involves outdated, unmaintained, or deprecated packages."
    )
    system_prompt = """
        You are a package maintenance and health specialist for Node.js dependencies.
        Your task: {hypothesis}
        Packages to focus on: {packages}
        Project context: {context}

        Available tools:
        {tool_descriptions}

        Investigation strategy:
        1. Call unmaintained_packages to identify packages with no recent commits or no active maintainer.
        2. Call high_risk_packages to flag packages with known risk signals (single maintainer, no tests, low downloads).
        3. For packages flagged by either tool, call package_reputation to get download trends and community signals.
        4. A package is a maintenance risk if: last commit > 12 months ago AND has open issues with no response, OR total weekly downloads dropped > 50% over 6 months.
        5. Record the package name, last release date, maintainer status, and risk rationale in each FindingNote.

        Rules:
        - Never repeat a tool call with the same arguments.
        - Set finalize=true when you have assessed all flagged packages.
        - After {max_iter} iterations, set finalize=true regardless.
        - confidence > 0.8: you have data for all focused packages.
        - confidence 0.5-0.8: partial data, some packages returned no results.
        - confidence < 0.5: tools returned errors or no data.
        """

    def _agent_tools(self) -> list:
        return [unmaintained_packages, high_risk_packages, package_reputation]
