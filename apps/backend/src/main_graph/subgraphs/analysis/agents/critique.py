from __future__ import annotations

import textwrap
from typing import cast

from pydantic import BaseModel

from src.models.conductor import FindingNote
from src.models.results import AgentDispatch
from src.utils.model_registry import AgentRole, get_role_llm

_llm = get_role_llm(AgentRole.ANALYSIS_CRITIQUE)

_SYSTEM = textwrap.dedent("""\
    You are an evidence auditor for a dependency risk investigation.
    You are given an agent's draft findings. Each finding has a claim
    (dependency, severity, description) and the evidence snippets the agent
    attached to justify it. Findings for packages with a known installed
    version are annotated "(installed: X.Y.Z)".

    Judge whether each finding is supported by its OWN attached evidence:
    - A finding with no evidence, or whose snippets do not back its claim,
      is unsupported.
    - A severity that overstates what the evidence shows is a defect.
    - A finding is also unsupported if the installed version is annotated
      and the evidence's own vulnerable-version range or patched/fixed
      version shows that version is not actually affected (e.g. "fixed in
      0.14.0" but installed is 0.14.1). Flag this even if the evidence text
      otherwise matches the claim.
    Do not investigate, do not add new findings, do not reason about other findings.

    Output a FindingsVerdict:
    - ok: true only if EVERY finding is adequately supported by its evidence.
    - feedback: concrete and actionable — name the finding and what is missing
      or overstated. Empty string when ok is true.
    - calibrated_confidence: 0.0-1.0 overall confidence based on evidence quality,
      independent of any self-reported score.
    """).strip()


class FindingsVerdict(BaseModel):
    ok: bool
    feedback: str
    calibrated_confidence: float


def _format_findings(
    findings: list[FindingNote], installed_versions: dict[str, str]
) -> str:
    parts = []
    for i, f in enumerate(findings, 1):
        if f.evidence:
            ev = "\n".join(f"    - [{e.tool}] {e.log_snippet}" for e in f.evidence)
        else:
            ev = "    (no evidence attached)"
        installed = installed_versions.get(f.dep_name)
        installed_note = f" (installed: {installed})" if installed else ""
        parts.append(
            f"{i}. {f.dep_name}{installed_note} [{f.severity}]: {f.description}\n"
            f"  evidence:\n{ev}"
        )
    return "\n\n".join(parts)


async def critique_findings(
    dispatch: AgentDispatch,
    findings: list[FindingNote],
    installed_versions: dict[str, str],
) -> FindingsVerdict:
    user = (
        f"Hypothesis under test: {dispatch.hypothesis}\n\n"
        f"Findings to verify:\n{_format_findings(findings, installed_versions)}"
    )
    structured = _llm.with_structured_output(FindingsVerdict, method="function_calling")
    return cast(
        FindingsVerdict,
        await structured.ainvoke(
            [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": user},
            ]
        ),
    )
