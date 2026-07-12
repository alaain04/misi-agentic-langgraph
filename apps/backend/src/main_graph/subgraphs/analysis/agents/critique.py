from __future__ import annotations

from pydantic import BaseModel

from src.models.conductor import FindingNote
from src.models.results import AgentDispatch
from src.utils.llm import Model, get_llm

_llm = get_llm(Model.GPT_5_4_MINI)

_SYSTEM = """\
You are an evidence auditor for a dependency risk investigation.
You are given an agent's draft findings. Each finding has a claim
(dependency, severity, description) and the evidence snippets the agent
attached to justify it.

Judge ONLY whether each finding is supported by its OWN attached evidence:
- A finding with no evidence, or whose snippets do not back its claim, is unsupported.
- A severity that overstates what the evidence shows is a defect.
Do not investigate, do not add new findings, do not reason about other findings.

Output a FindingsVerdict:
- ok: true only if EVERY finding is adequately supported by its evidence.
- feedback: concrete and actionable — name the finding and what is missing or overstated.
  Empty string when ok is true.
- calibrated_confidence: 0.0-1.0 overall confidence based on evidence quality,
  independent of any self-reported score.
"""


class FindingsVerdict(BaseModel):
    ok: bool
    feedback: str
    calibrated_confidence: float


def _format_findings(findings: list[FindingNote]) -> str:
    parts = []
    for i, f in enumerate(findings, 1):
        if f.evidence:
            ev = "\n".join(f"    - [{e.tool}] {e.log_snippet}" for e in f.evidence)
        else:
            ev = "    (no evidence attached)"
        parts.append(
            f"{i}. {f.dep_name} [{f.severity}]: {f.description}\n"
            f"  evidence:\n{ev}"
        )
    return "\n\n".join(parts)


async def critique_findings(
    dispatch: AgentDispatch, findings: list[FindingNote]
) -> FindingsVerdict:
    user = (
        f"Hypothesis under test: {dispatch.hypothesis}\n\n"
        f"Findings to verify:\n{_format_findings(findings)}"
    )
    structured = _llm.with_structured_output(FindingsVerdict, method="function_calling")
    return await structured.ainvoke([
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user},
    ])
