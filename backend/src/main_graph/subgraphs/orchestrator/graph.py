from langgraph.graph import START, StateGraph

from src.main_graph.subgraphs.orchestrator.nodes import orchestrator, planner
from src.main_graph.subgraphs.orchestrator.state import OrchestratorState


def build_orchestrator_subgraph():
    builder = StateGraph(OrchestratorState)

    builder.add_node("planner", planner)
    builder.add_node("orchestrator", orchestrator)

    builder.add_edge(START, "planner")
    builder.add_edge("planner", "orchestrator")
    # orchestrator returns Command(goto="planner") on "change",
    # Command(goto=END) on "cancel", or plain dict on "approve"

    return builder.compile()


orchestrator_subgraph = build_orchestrator_subgraph()
