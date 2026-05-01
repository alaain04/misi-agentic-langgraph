from typing_extensions import TypedDict


class RecommenderState(TypedDict):
    review: str
    recommendation: str
