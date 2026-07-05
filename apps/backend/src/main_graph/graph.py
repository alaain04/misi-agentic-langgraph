from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from src.main_graph.state import MainState


def build_main_graph():
    builder = StateGraph(MainState)
    # Nodes will be added in Task 13; stub routes START directly to END
    builder.add_edge(START, END)
    return builder.compile(checkpointer=InMemorySaver())


main_graph = build_main_graph()
