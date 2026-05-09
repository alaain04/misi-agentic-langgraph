"""Supply chain subgraph builder."""

from langgraph.graph import END, START, StateGraph

from src.main_graph.subgraphs.ingestion_subgraphs.supply_chain.constants import ANALYZE
from src.main_graph.subgraphs.ingestion_subgraphs.supply_chain.nodes import analyze
from src.main_graph.subgraphs.ingestion_subgraphs.supply_chain.state import (
    SupplyChainState,
)

GRAPH_NAME = "supply_chain"
DEPENDS_ON: list[str] = []


def describe() -> str:
    return (
        f"{GRAPH_NAME}:Evaluates maintainer reputation, package age,"
        " download trends, and typosquatting risks across dependencies"
    )


def build_supply_chain_subgraph() -> StateGraph:
    builder = StateGraph(SupplyChainState)
    builder.add_node(ANALYZE, analyze)
    builder.add_edge(START, ANALYZE)
    builder.add_edge(ANALYZE, END)
    return builder.compile()


supply_chain_subgraph = build_supply_chain_subgraph()
