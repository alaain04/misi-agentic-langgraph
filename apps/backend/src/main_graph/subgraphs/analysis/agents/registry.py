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

# Public registry: agent_type → tool name list (for description/logging)
AGENT_REGISTRY: dict[str, list[str]] = {
    k: [name for _, name in v] for k, v in _TOOL_IMPORTS.items()
}


def _import_tool(module_path: str, fn_name: str):
    import importlib
    mod = importlib.import_module(module_path)
    return getattr(mod, fn_name)


def get_agent_tools(agent_type: str, prep: PrepResult) -> list:
    """Return configured LangChain tools for the given agent_type."""
    imports = _TOOL_IMPORTS.get(agent_type, _TOOL_IMPORTS["web_research_agent"])
    tools = [_import_tool(mod, fn) for mod, fn in imports]

    if prep.vector_store_id:
        tools.append(make_search_code_tool(prep.vector_store_id))

    return tools
