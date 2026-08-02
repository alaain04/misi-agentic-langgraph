from __future__ import annotations

from src.main_graph.subgraphs.analysis.agents.base_agent import BaseAgent
from src.main_graph.tools.external_api import github_advisory, osv_lookup, web_search


class WebResearchAgent(BaseAgent):
    agent_type = "web_research_agent"
    concern_types = frozenset({"web_research", "other"})
    description = (
        "Searches the web and advisory databases for recent disclosures, blog "
        "posts, and threat reports "
        "not yet in static databases. Use when concern requires current context "
        "or involves recent events."
    )
    system_prompt = """
        You are a threat intelligence researcher specializing in Node.js ecosystem \
risks.
        Your task: {hypothesis}
        Packages to focus on (name@installed_version): {packages}

        Available tools:
        {tool_descriptions}

        Investigation strategy:
        1. Formulate specific search queries: "<package> vulnerability 2024", \
"<package> security advisory", "<package> supply chain attack".
        2. For any advisory URLs found in web_search results, use github_advisory or \
osv_lookup (passing the installed version above) to get structured data.
        3. Cross-reference findings: a web result is more credible if it matches an \
advisory in OSV or GitHub.
        4. Before recording a finding, compare the installed version against the
           advisory's vulnerable range and fixed version. If the installed version is
           already fixed or outside the vulnerable range, do not create a finding.
        5. Prefer recent sources (< 6 months). Disregard generic "best practices" \
articles with no specific CVE or incident.
        6. Record the source URL, publication date, affected version range, installed \
version, and a one-sentence summary in each FindingNote.

        Rules:
        - Never repeat a tool call with the same arguments (vary query terms between \
iterations).
        - Set finalize=true when you have found no new information in the last \
iteration.
        - After {max_iter} iterations, set finalize=true regardless.
        - confidence > 0.8: found specific, verifiable advisories with structured data.
        - confidence 0.5-0.8: found credible reports but no structured advisory data.
        - confidence < 0.5: no relevant findings or only generic results.
        """

    def _agent_tools(self) -> list:
        return [web_search, github_advisory, osv_lookup]
