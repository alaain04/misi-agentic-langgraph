import sys

from src.main_graph.graph import main_graph
from src.main_graph.subgraphs.discovery.graph import discovery_subgraph


def draw_png_graph(graph):
    graph = graph.get_graph().draw_mermaid_png(output_file_path="graph.png")


def draw_mermaid(graph):
    mermaid = graph.get_graph().draw_mermaid()
    print(mermaid)


MODES = {
    "mermaid": lambda graph: draw_mermaid(graph),
    "png": lambda graph: draw_png_graph(graph),
}

GRAPHS = {
    "discovery": discovery_subgraph,
    "main": main_graph,
}

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "mermaid"
    graph_name = sys.argv[2] if len(sys.argv) > 2 else "main"

    if graph_name not in GRAPHS:
        print(f"Unknown graph '{graph_name}'. Choose from: {', '.join(GRAPHS.keys())}")
        sys.exit(1)
    if mode not in MODES:
        print(f"Unknown mode '{mode}'. Choose from: {', '.join(MODES)}")
        sys.exit(1)

    print(f"Running mode: {mode}")
    MODES[mode](GRAPHS[graph_name])
