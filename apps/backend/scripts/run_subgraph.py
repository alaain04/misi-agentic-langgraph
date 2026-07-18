#!/usr/bin/env python3
"""
Manually run any single subgraph against a real MongoDB and real LLMs.

Usage:
    # Run discovery (clones a real repo, needs Docker):
    uv run python scripts/run_subgraph.py discovery \\
        --repo https://github.com/expressjs/express \\
        --concern "security vulnerabilities"

    # Run analysis (needs a prep_result_id from a previous discovery run):
    uv run python scripts/run_subgraph.py analysis \\
        --prep-result-id <id> \\
        --concern "security vulnerabilities"

    # Run report (needs an analysis_result_id):
    uv run python scripts/run_subgraph.py report \\
        --analysis-result-id <id> \\
        --concern "security vulnerabilities"

All subgraphs read/write from the MongoDB configured via MONGODB_URI.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid

from langchain_core.runnables import RunnableConfig

# Ensure src is importable from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


async def _run_discovery(args) -> None:
    from src.db.result_dao import ResultDAO
    from src.main_graph.adapters.docker_container_adapter import DockerContainerAdapter
    from src.main_graph.subgraphs.discovery.graph import build_discovery_subgraph

    job_id = args.job_id or str(uuid.uuid4())[:8]
    dao = ResultDAO()

    container = DockerContainerAdapter()

    config: RunnableConfig = {
        "configurable": {
            "result_dao": dao,
            "container": container,
            "docker_tool": None,
            "job_repo": None,
        }
    }

    print(f"[discovery] job_id={job_id}  repo={args.repo}")
    graph = build_discovery_subgraph()
    result = await graph.ainvoke(
        {
            "job_id": job_id,
            "repo_url": args.repo,
            "concern": args.concern,
            "autopilot": True,
        },
        config=config,
    )

    if result.get("discovery_error"):
        print(f"[discovery] ERROR: {result['discovery_error']}")
        return

    prep_id = result.get("prep_result_id")
    assert prep_id is not None
    print(f"[discovery] prep_result_id = {prep_id}")
    print(f"[discovery] vector_store_id = {result.get('vector_store_id', '')}")

    prep = await dao.get_prep(prep_id)
    print("\n--- PrepResult ---")
    print(f"  package_manager : {prep.detected_package_manager}")
    direct_deps = list(prep.dependency_graph.get("direct", {}).keys())[:10]
    print(f"  direct deps     : {direct_deps}")
    print(f"  summary (first 200 chars): {prep.discovery_summary[:200]}")


async def _run_analysis(args) -> None:
    from src.db.result_dao import ResultDAO
    from src.main_graph.subgraphs.analysis.graph import build_analysis_subgraph

    job_id = args.job_id or str(uuid.uuid4())[:8]
    dao = ResultDAO()

    config: RunnableConfig = {
        "configurable": {
            "result_dao": dao,
            "container": None,
            "docker_tool": None,
            "job_repo": None,
        }
    }

    print(f"[analysis] job_id={job_id}  prep_result_id={args.prep_result_id}")
    graph = build_analysis_subgraph()
    result = await graph.ainvoke(
        {
            "job_id": job_id,
            "concern": args.concern,
            "prep_result_id": args.prep_result_id,
            "bundle_ids": [],
        },
        config=config,
    )

    analysis_id = result.get("analysis_result_id")
    print(f"[analysis] analysis_result_id = {analysis_id}")

    analysis = await dao.get_analysis(analysis_id)
    print("\n--- AnalysisResult ---")
    print(f"  findings        : {len(analysis.findings)}")
    print(f"  iterations      : {analysis.iteration_count}")
    for f in analysis.findings:
        print(f"  [{f.severity.upper():8s}] {f.dep_name}: {f.description[:60]}")


async def _run_report(args) -> None:
    from src.db.result_dao import ResultDAO
    from src.main_graph.subgraphs.report.graph import build_report_subgraph

    job_id = args.job_id or str(uuid.uuid4())[:8]
    dao = ResultDAO()

    config: RunnableConfig = {
        "configurable": {
            "result_dao": dao,
            "container": None,
            "docker_tool": None,
            "job_repo": None,
        }
    }

    print(f"[report] job_id={job_id}  analysis_result_id={args.analysis_result_id}")
    await dao.get_analysis(args.analysis_result_id)

    graph = build_report_subgraph()
    result = await graph.ainvoke(
        {
            "job_id": job_id,
            "concern": args.concern,
            "prep_result_id": "",
            "analysis_result_id": args.analysis_result_id,
            "tool_results": [],
        },
        config=config,
    )

    report_id = result.get("report_result_id")
    print(f"[report] report_result_id = {report_id}")

    report = await dao.get_report(report_id)
    print("\n--- ReportResult ---")
    print(f"  risk_level      : {report.overall_risk_level}")
    print(f"  findings        : {len(report.findings)}")
    print(f"  recommendations : {len(report.recommendations)}")
    print(f"\nExecutive summary:\n{report.executive_summary}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a single subgraph against real infrastructure"
    )
    parser.add_argument("subgraph", choices=["discovery", "analysis", "report"])
    parser.add_argument("--concern", required=True, help="User concern text")
    parser.add_argument("--job-id", help="Optional job ID (generated if omitted)")

    parser.add_argument("--repo", help="[discovery] GitHub repo URL")
    parser.add_argument(
        "--prep-result-id", help="[analysis] PrepResult ID from a prior discovery run"
    )
    parser.add_argument(
        "--analysis-result-id",
        help="[report] AnalysisResult ID from a prior analysis run",
    )

    args = parser.parse_args()

    if args.subgraph == "discovery" and not args.repo:
        parser.error("--repo is required for discovery")
    if args.subgraph == "analysis" and not args.prep_result_id:
        parser.error("--prep-result-id is required for analysis")
    if args.subgraph == "report" and not args.analysis_result_id:
        parser.error("--analysis-result-id is required for report")

    runners = {
        "discovery": _run_discovery,
        "analysis": _run_analysis,
        "report": _run_report,
    }

    asyncio.run(runners[args.subgraph](args))


if __name__ == "__main__":
    main()
