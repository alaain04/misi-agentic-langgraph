"""Final report node — aggregates all subgraph results into a text report."""

from src.graphs.main_graph.state import MainState


async def final_report(state: MainState) -> dict:
    metadata = state.get("project_metadata") or {}
    project_name = metadata.get("name", "unknown")
    pm = metadata.get("package_manager", "unknown")
    summary = state.get("discovery_summary", "No discovery summary available.")
    results = state.get("subgraph_results", [])

    lines = [
        f"# Dependency Analysis Report: {project_name}",
        f"Package Manager: {pm}",
        "",
        "## Discovery Summary",
        summary,
        "",
        "## Subgraph Results",
    ]

    for entry in results:
        name = entry.get("subgraph", "unknown")
        data = entry.get("data", {})
        error = entry.get("error")
        lines.append(f"\n### {name.replace('_', ' ').title()}")
        if error:
            lines.append(f"Error: {error}")
        else:
            result_key = f"{name}_result"
            result_data = data.get(result_key, data)
            lines.append(f"Status: {result_data.get('status', 'unknown')}")
            note = result_data.get("note")
            if note:
                lines.append(f"Note: {note}")

    return {"final_report": "\n".join(lines)}
