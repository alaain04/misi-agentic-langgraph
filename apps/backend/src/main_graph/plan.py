from typing import NotRequired
from typing_extensions import TypedDict


class Plan(TypedDict):
    subgraphs: list[str]
    dep_filter: NotRequired[list[str] | None]
