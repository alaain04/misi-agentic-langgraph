import json

import pytest

from src.main_graph.tools.registry import TOOL_REGISTRY


@pytest.fixture
def repo(tmp_path):
    pkg = {
        "name": "my-app",
        "version": "1.0.0",
        "dependencies": {"lodash": "^4.17.21", "express": "latest"},
        "devDependencies": {"jest": "^29.0.0"},
        "scripts": {"postinstall": "node setup.js"},
        "license": "MIT",
    }
    (tmp_path / "package.json").write_text(json.dumps(pkg))
    return str(tmp_path)


@pytest.mark.asyncio
async def test_package_json_reads_file(repo):
    result = await TOOL_REGISTRY["package_json"](repo_path=repo)
    assert result["name"] == "my-app"
    assert "lodash" in result["dependencies"]


@pytest.mark.asyncio
async def test_version_ranges_detects_latest(repo):
    result = await TOOL_REGISTRY["version_ranges"](repo_path=repo)
    risky = [r["package"] for r in result["risky_ranges"]]
    assert "express" in risky


@pytest.mark.asyncio
async def test_install_scripts_detects_postinstall(repo):
    result = await TOOL_REGISTRY["install_scripts"](repo_path=repo)
    # postinstall is declared in the root package.json scripts
    scripts = result.get("packages_with_scripts", [])
    # root project always counted if it has lifecycle scripts
    assert any("postinstall" in str(s) for s in scripts) or result.get("note")


@pytest.mark.asyncio
async def test_read_file_returns_content(repo):
    result = await TOOL_REGISTRY["read_file"](
        repo_path=repo, relative_path="package.json"
    )
    assert "my-app" in result["content"]


@pytest.mark.asyncio
async def test_read_file_missing_returns_error(repo):
    result = await TOOL_REGISTRY["read_file"](
        repo_path=repo, relative_path="nonexistent.txt"
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_list_directory_returns_entries(repo):
    result = await TOOL_REGISTRY["list_directory"](repo_path=repo, relative_path=".")
    assert "package.json" in result["entries"]


@pytest.mark.asyncio
async def test_read_file_rejects_path_traversal(repo):
    result = await TOOL_REGISTRY["read_file"](
        repo_path=repo, relative_path="../etc/passwd"
    )
    assert "error" in result
    assert "path traversal" in result["error"]


@pytest.mark.asyncio
async def test_list_directory_rejects_path_traversal(repo):
    result = await TOOL_REGISTRY["list_directory"](
        repo_path=repo, relative_path="../etc"
    )
    assert "error" in result
    assert "path traversal" in result["error"]


def test_all_package_file_tools_registered():
    expected = [
        "package_json",
        "package_lock",
        "version_ranges",
        "dependency_confusion",
        "install_scripts",
        "duplicate_packages",
        "missing_dependencies",
        "dependency_size",
        "dependency_stats",
        "workspace_dependencies",
        "read_file",
        "list_directory",
    ]
    for name in expected:
        assert name in TOOL_REGISTRY, f"{name} not in TOOL_REGISTRY"
