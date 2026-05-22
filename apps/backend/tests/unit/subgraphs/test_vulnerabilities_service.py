import json
from unittest.mock import AsyncMock

import pytest

from src.domain.ports.container_run_port import ContainerRunPort
from src.domain.ports.ingestion_result_port import IngestionResultPort


@pytest.mark.asyncio
async def test_analyze_service_saves_records():
    from src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.service import (
        analyze_service,
    )

    trivy_output = {
        "Results": [
            {
                "Vulnerabilities": [
                    {
                        "PkgName": "lodash",
                        "InstalledVersion": "4.17.15",
                        "VulnerabilityID": "CVE-2021-23337",
                        "Severity": "HIGH",
                        "Description": "Prototype pollution",
                        "FixedVersion": "4.17.21",
                    }
                ]
            }
        ]
    }
    container = AsyncMock(spec=ContainerRunPort)
    container.run.return_value = (0, json.dumps(trivy_output), "")
    dao = AsyncMock(spec=IngestionResultPort)
    dao.save.return_value = "result_abc"

    state = {"repo_path": "/tmp/repo", "concern": "security"}
    result = await analyze_service(state, container, dao)

    assert result == {"result_id": "result_abc"}
    dao.save.assert_awaited_once()
    saved_entry = dao.save.call_args[0][0]
    assert saved_entry.total_findings == 1
    assert saved_entry.records[0].name == "lodash"


@pytest.mark.asyncio
async def test_analyze_service_no_repo_path():
    from src.main_graph.subgraphs.ingestion_subgraphs.vulnerabilities.service import (
        analyze_service,
    )

    container = AsyncMock(spec=ContainerRunPort)
    dao = AsyncMock(spec=IngestionResultPort)
    dao.save.return_value = "empty_id"

    result = await analyze_service({"repo_path": "", "concern": "sec"}, container, dao)

    assert result == {"result_id": "empty_id"}
    container.run.assert_not_awaited()
