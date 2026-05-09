import sys

from src.main_graph.graph import main_graph


def draw_png_graph():
    graph = main_graph.get_graph()
    graph.draw_mermaid_png(output_file_path="graph.png")


def draw_mermaid():
    graph = main_graph.get_graph()
    print(graph.draw_mermaid())


MODES = {
    "mermaid": lambda: draw_mermaid(),
    "png": lambda: draw_png_graph(),
}

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "mermaid"
    if mode not in MODES:
        print(f"Unknown mode '{mode}'. Choose from: {', '.join(MODES)}")
        sys.exit(1)

    print(f"Running mode: {mode}")
    MODES[mode]()
