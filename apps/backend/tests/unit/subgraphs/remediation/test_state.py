def test_remediation_state_accepts_new_deepagent_fields():
    from src.main_graph.subgraphs.remediation.state import RemediationState

    state: RemediationState = {
        "job_id": "j1",
        "concern": "c",
        "prep_result_id": "p1",
        "analysis_result_id": "a1",
        "targets": {},
        "evidence": {},
        "remediations": {},
        "requires_edges": {},
        "retry_targets": [],
        "correction_rounds": 0,
    }
    assert state["correction_rounds"] == 0
