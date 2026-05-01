from typing_extensions import TypedDict


class SummarizerState(TypedDict):
    subgraph_results: list[dict]
    summary: str
