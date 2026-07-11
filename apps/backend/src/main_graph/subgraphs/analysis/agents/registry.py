from __future__ import annotations
from src.models.results import PrepResult
from src.main_graph.tools.search_code import make_search_code_tool

# domain agent_type → list of (module_path, function_name) tuples
_TOOL_IMPORTS: dict[str, list[tuple[str, str]]] = {
    "vulnerability_agent": [
        ("src.main_graph.tools.npm_cli", "npm_audit"),
        ("src.main_graph.tools.external_api", "osv_lookup"),
        ("src.main_graph.tools.external_api", "github_advisory"),
    ],
    "maintenance_agent": [
        ("src.main_graph.tools.external_api", "unmaintained_packages"),
        ("src.main_graph.tools.external_api", "high_risk_packages"),
        ("src.main_graph.tools.external_api", "package_reputation"),
    ],
    "supply_chain_agent": [
        ("src.main_graph.tools.external_api", "typosquat_detection"),
        ("src.main_graph.tools.npm_cli", "resolve_transitive_parent"),
        ("src.main_graph.tools.package_files", "package_json"),
    ],
    "web_research_agent": [
        ("src.main_graph.tools.external_api", "web_search"),
        ("src.main_graph.tools.external_api", "github_advisory"),
        ("src.main_graph.tools.external_api", "osv_lookup"),
    ],
}

_AGENT_SPECS: dict[str, dict[str, str]] = {
    "vulnerability_agent": {
        "description": (
            "Checks for known CVEs, security advisories, and exploits via npm audit, OSV, and GitHub advisories. "
            "Use when the concern involves security vulnerabilities, CVE IDs, or active exploit risk."
        ),
        "system_prompt": """\
You are a security vulnerability specialist for Node.js dependencies.
Your task: {hypothesis}
Packages to focus on: {packages}
Project context: {context}

Available tools:
{tool_descriptions}

Investigation strategy:
1. Run npm_audit first to get the baseline CVE list for the project.
2. For each HIGH or CRITICAL severity finding, call osv_lookup and github_advisory to get full advisory details.
3. Check CVSS score, affected version ranges, and whether a fix version exists.
4. A finding is significant if: severity >= HIGH, the package is a direct dependency, and no fix is available or the fix requires a major-version bump.
5. Record the CVE ID, severity, CVSS score, affected version, and recommended action in each FindingNote.

Rules:
- Never repeat a tool call with the same arguments.
- Set finalize=true when you have checked all HIGH/CRITICAL findings or exhausted relevant tools.
- After {max_iter} iterations, set finalize=true regardless.
- confidence > 0.8: you audited all high-severity packages with advisory data.
- confidence 0.5-0.8: partial audit, some packages could not be checked.
- confidence < 0.5: audit failed or no data available.
""",
    },
    "maintenance_agent": {
        "description": (
            "Assesses package health and abandonment risk by checking maintenance status, download trends, "
            "and known high-risk packages. Use when concern involves outdated, unmaintained, or deprecated packages."
        ),
        "system_prompt": """\
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
""",
    },
    "supply_chain_agent": {
        "description": (
            "Detects supply-chain attacks: typosquatting, dependency confusion, malicious transitive dependencies, "
            "and suspicious package metadata. Use when concern involves supply-chain integrity or malicious packages."
        ),
        "system_prompt": """\
You are a supply-chain security specialist for Node.js dependencies.
Your task: {hypothesis}
Packages to focus on: {packages}
Project context: {context}

Available tools:
{tool_descriptions}

Investigation strategy:
1. Call typosquat_detection on the package names most similar to popular packages (e.g. "lodash" vs "1odash").
2. Call package_json to inspect package metadata for suspicious fields: postinstall scripts, unusual authors, mismatched repository URLs.
3. Call resolve_transitive_parent for any package flagged as suspicious to trace which direct dependency pulled it in.
4. A finding is significant if: a package name is a known typosquat of a popular package, OR the package has a postinstall script with no legitimate reason, OR the author/repository metadata is inconsistent.
5. Record the package name, attack vector (typosquat/confusion/malicious script), and the transitive chain in each FindingNote.

Rules:
- Never repeat a tool call with the same arguments.
- Set finalize=true when you have checked all focused packages for supply-chain indicators.
- After {max_iter} iterations, set finalize=true regardless.
- confidence > 0.8: all focused packages inspected with no gaps.
- confidence 0.5-0.8: partial inspection, some packages not checked.
- confidence < 0.5: tools returned errors or packages not found.
""",
    },
    "web_research_agent": {
        "description": (
            "Searches the web and advisory databases for recent disclosures, blog posts, and threat reports "
            "not yet in static databases. Use when concern requires current context or involves recent events."
        ),
        "system_prompt": """\
You are a threat intelligence researcher specializing in Node.js ecosystem risks.
Your task: {hypothesis}
Packages to focus on: {packages}
Project context: {context}

Available tools:
{tool_descriptions}

Investigation strategy:
1. Formulate specific search queries: "<package> vulnerability 2024", "<package> security advisory", "<package> supply chain attack".
2. For any advisory URLs found in web_search results, use github_advisory or osv_lookup to get structured data.
3. Cross-reference findings: a web result is more credible if it matches an advisory in OSV or GitHub.
4. Prefer recent sources (< 6 months). Disregard generic "best practices" articles with no specific CVE or incident.
5. Record the source URL, publication date, affected version, and a one-sentence summary in each FindingNote.

Rules:
- Never repeat a tool call with the same arguments (vary query terms between iterations).
- Set finalize=true when you have found no new information in the last iteration.
- After {max_iter} iterations, set finalize=true regardless.
- confidence > 0.8: found specific, verifiable advisories with structured data.
- confidence 0.5-0.8: found credible reports but no structured advisory data.
- confidence < 0.5: no relevant findings or only generic results.
""",
    },
}

# Public registry: agent_type → tool name list (for description/logging)
AGENT_REGISTRY: dict[str, list[str]] = {
    k: [name for _, name in v] for k, v in _TOOL_IMPORTS.items()
}


def _import_tool(module_path: str, fn_name: str):
    import importlib
    mod = importlib.import_module(module_path)
    return getattr(mod, fn_name)


def get_agent_tools(agent_type: str, prep: PrepResult) -> list:
    """Return tools for the given agent_type."""
    imports = _TOOL_IMPORTS.get(agent_type, _TOOL_IMPORTS["web_research_agent"])
    tools = [_import_tool(mod, fn) for mod, fn in imports]
    if prep.vector_store_id:
        tools.append(make_search_code_tool(prep.vector_store_id))
    return tools


def get_agent_prompt(agent_type: str) -> str | None:
    """Return the specialized system prompt for the given agent_type, or None."""
    spec = _AGENT_SPECS.get(agent_type)
    return spec["system_prompt"] if spec else None


def get_agent_descriptions() -> dict[str, str]:
    """Return agent_type → description mapping for the conductor."""
    return {k: v["description"] for k, v in _AGENT_SPECS.items()}
