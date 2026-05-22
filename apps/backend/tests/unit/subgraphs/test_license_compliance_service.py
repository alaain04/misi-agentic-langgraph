import json
from unittest.mock import AsyncMock

import pytest

from src.domain.ports.container_run_port import ContainerRunPort
from src.domain.ports.ingestion_result_port import IngestionResultPort


@pytest.mark.asyncio
async def test_analyze_service_records_violations():
    from src.main_graph.subgraphs.ingestion_subgraphs.license_compliance.service import (
        analyze_service,
    )

    trivy_output = {
        "Results": [
            {
                "Licenses": [
                    {"PkgName": "react", "Name": "GPL-2.0", "Category": "restricted"},
                    {"PkgName": "lodash", "Name": "MIT", "Category": "permissive"},
                ]
            }
        ]
    }
    container = AsyncMock(spec=ContainerRunPort)
    container.run.return_value = (0, json.dumps(trivy_output), "")
    dao = AsyncMock(spec=IngestionResultPort)
    dao.save.return_value = "lic_id"

    result = await analyze_service({"repo_path": "/r", "concern": "licenses"}, container, dao)

    assert result == {"result_id": "lic_id"}
    entry = dao.save.call_args[0][0]
    assert entry.total_violations == 1
