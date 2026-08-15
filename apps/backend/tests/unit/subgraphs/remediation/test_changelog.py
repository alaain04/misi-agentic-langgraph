from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.main_graph.subgraphs.remediation.changelog import (
    _tag_in_window,
    _tag_version,
    fetch_release_notes,
    fetch_release_notes_between,
    fetch_release_notes_page,
    resolve_package_info,
)


class FakeContainer:
    """Returns queued (rc, stdout, stderr) per run() call, in order."""

    def __init__(self, results):
        self._results = list(results)
        self.commands = []
        self.calls = []

    async def run(
        self, image, command, volume=None, run_as_root=False, secret_env=None
    ):
        self.commands.append(command)
        self.calls.append(
            {
                "image": image,
                "command": command,
                "volume": volume,
                "run_as_root": run_as_root,
                "secret_env": secret_env,
            }
        )
        return self._results.pop(0)


@pytest.mark.asyncio
async def test_fetch_release_notes_returns_unavailable_when_repo_unresolved():
    container = FakeContainer([(1, "", "npm error 404 Not Found")])
    result = await fetch_release_notes(
        "left-pad", "/repo", container, "node:lts-alpine"
    )
    assert result["available"] is False


@pytest.mark.asyncio
async def test_fetch_release_notes_success():
    releases_json = json.dumps(
        [{"tag_name": "v9.0.0", "name": "9.0.0", "body": "breaking: flat config"}]
    )
    container = FakeContainer(
        [
            (0, "git+https://github.com/eslint/eslint.git\n", ""),
            (0, releases_json, ""),
        ]
    )
    with patch(
        "src.main_graph.subgraphs.remediation.changelog.settings"
    ) as mock_settings:
        mock_settings.gh_docker_image = "gh-cli:latest"
        mock_settings.github_token = "ghp_test"
        result = await fetch_release_notes(
            "eslint", "/repo", container, "node:lts-alpine"
        )

    assert result["available"] is True
    assert result["repository"] == "eslint/eslint"
    assert result["releases"][0]["tag"] == "v9.0.0"
    # Second call is the containerized gh api lookup, not a host subprocess.
    assert container.calls[1]["image"] == "gh-cli:latest"
    assert "gh api" in container.calls[1]["command"]
    assert container.calls[1]["secret_env"] == {"GH_TOKEN": "ghp_test"}


@pytest.mark.asyncio
async def test_fetch_release_notes_uses_resolved_repo_skips_npm_view():
    """When the caller already resolved (owner, repo) -- e.g.
    classify_targets_node via resolve_package_info -- fetch_release_notes
    must not spawn a second npm view container for the same package."""
    releases_json = json.dumps([])
    container = FakeContainer([(0, releases_json, "")])
    with patch(
        "src.main_graph.subgraphs.remediation.changelog.settings"
    ) as mock_settings:
        mock_settings.gh_docker_image = "gh-cli:latest"
        mock_settings.github_token = ""
        result = await fetch_release_notes(
            "eslint",
            "/repo",
            container,
            "node:lts-alpine",
            resolved_repo=("eslint", "eslint"),
        )

    assert result["available"] is True
    assert len(container.calls) == 1
    assert "gh api" in container.calls[0]["command"]


@pytest.mark.asyncio
async def test_fetch_release_notes_omits_secret_env_without_token():
    container = FakeContainer(
        [
            (0, "git+https://github.com/eslint/eslint.git\n", ""),
            (0, "[]", ""),
        ]
    )
    with patch(
        "src.main_graph.subgraphs.remediation.changelog.settings"
    ) as mock_settings:
        mock_settings.gh_docker_image = "gh-cli:latest"
        mock_settings.github_token = ""
        await fetch_release_notes("eslint", "/repo", container, "node:lts-alpine")

    assert container.calls[1]["secret_env"] is None


@pytest.mark.asyncio
async def test_fetch_release_notes_gh_failure_reports_unavailable():
    container = FakeContainer(
        [
            (0, "git+https://github.com/eslint/eslint.git\n", ""),
            (1, "", "HTTP 404: Not Found"),
        ]
    )
    with patch(
        "src.main_graph.subgraphs.remediation.changelog.settings"
    ) as mock_settings:
        mock_settings.gh_docker_image = "gh-cli:latest"
        mock_settings.github_token = ""
        result = await fetch_release_notes(
            "eslint", "/repo", container, "node:lts-alpine"
        )

    assert result["available"] is False
    assert "404" in result["error"]


@pytest.mark.asyncio
async def test_fetch_release_notes_safely_quotes_package_name():
    """Shell metacharacters in a package name must not reach the container
    command unescaped (command injection guard)."""
    container = FakeContainer([(1, "", "npm error 404")])
    malicious_package = "eslint; rm -rf /"
    result = await fetch_release_notes(
        malicious_package, "/repo", container, "node:lts-alpine"
    )

    assert len(container.commands) == 1
    executed_command = container.commands[0]
    assert "'eslint; rm -rf /'" in executed_command or (
        "eslint" in executed_command and "rm -rf" not in executed_command
    )
    assert result["available"] is False


def test_tag_version_strips_v_prefix():
    assert _tag_version("v4.17.21") == (4, 17, 21)
    assert _tag_version("4.17.21") == (4, 17, 21)
    assert _tag_version("release-1.2") is None


def test_tag_in_window_half_open():
    # (1.0.0, 2.0.0]: excludes current, includes target
    assert _tag_in_window("v1.0.0", (1, 0, 0), (2, 0, 0)) is False
    assert _tag_in_window("v1.5.0", (1, 0, 0), (2, 0, 0)) is True
    assert _tag_in_window("v2.0.0", (1, 0, 0), (2, 0, 0)) is True
    assert _tag_in_window("v2.0.1", (1, 0, 0), (2, 0, 0)) is False


@pytest.mark.asyncio
async def test_fetch_between_filters_to_window():
    full = {
        "package_name": "lodash",
        "available": True,
        "repository": "lodash/lodash",
        "releases": [
            {"tag": "v2.0.0", "name": "2", "body": "b"},
            {"tag": "v1.5.0", "name": "1.5", "body": "b"},
            {"tag": "v1.0.0", "name": "1", "body": "b"},
        ],
    }
    with patch(
        "src.main_graph.subgraphs.remediation.changelog.fetch_release_notes",
        AsyncMock(return_value=full),
    ):
        out = await fetch_release_notes_between(
            "lodash", "1.0.0", "2.0.0", "/tmp/repo", None, "img"
        )
    tags = [r["tag"] for r in out["releases"]]
    assert tags == ["v2.0.0", "v1.5.0"]  # v1.0.0 excluded (half-open)


@pytest.mark.asyncio
async def test_fetch_between_unparseable_bounds_returns_unfiltered():
    full = {
        "package_name": "lodash",
        "available": True,
        "releases": [{"tag": "v1.5.0", "name": "x", "body": "b"}],
    }
    with patch(
        "src.main_graph.subgraphs.remediation.changelog.fetch_release_notes",
        AsyncMock(return_value=full),
    ):
        out = await fetch_release_notes_between(
            "lodash", None, None, "/tmp/repo", None, "img"
        )
    assert out["releases"] == full["releases"]


@pytest.mark.asyncio
async def test_resolve_package_info_returns_version_and_repo_from_one_call():
    container = FakeContainer(
        [
            (
                0,
                json.dumps(
                    {
                        "version": "4.17.21",
                        "repository.url": "git+https://github.com/lodash/lodash.git",
                    }
                ),
                "",
            )
        ]
    )

    version, repo = await resolve_package_info(
        "lodash", "/tmp/repo", container, "node:lts-alpine"
    )

    assert version == "4.17.21"
    assert repo == ("lodash", "lodash")
    # Exactly one npm view call for both facts.
    assert len(container.calls) == 1
    assert "version" in container.calls[0]["command"]
    assert "repository.url" in container.calls[0]["command"]


@pytest.mark.asyncio
async def test_resolve_package_info_handles_missing_repository_field():
    container = FakeContainer([(0, json.dumps({"version": "1.3.0"}), "")])

    version, repo = await resolve_package_info(
        "no-repo-pkg", "/tmp/repo", container, "node:lts-alpine"
    )

    assert version == "1.3.0"
    assert repo is None


@pytest.mark.asyncio
async def test_resolve_package_info_none_on_npm_failure():
    container = FakeContainer([(1, "", "npm error 404")])

    version, repo = await resolve_package_info(
        "ghost-pkg", "/tmp/repo", container, "node:lts-alpine"
    )

    assert version is None
    assert repo is None


@pytest.mark.asyncio
async def test_resolve_package_info_none_on_unparseable_json():
    container = FakeContainer([(0, "not json", "")])

    version, repo = await resolve_package_info(
        "lodash", "/tmp/repo", container, "node:lts-alpine"
    )

    assert version is None
    assert repo is None


@pytest.mark.asyncio
async def test_resolve_package_info_none_when_container_raises():
    container = MagicMock()
    container.run = AsyncMock(side_effect=RuntimeError("docker down"))

    version, repo = await resolve_package_info(
        "lodash", "/tmp/repo", container, "node:lts-alpine"
    )

    assert version is None
    assert repo is None


@pytest.mark.asyncio
async def test_fetch_release_notes_page_windows_and_reports_no_more():
    releases_json = json.dumps(
        [
            {"tag_name": "v9.0.0", "name": "9.0.0", "body": "flat config"},
            {"tag_name": "v8.5.0", "name": "8.5.0", "body": "minor"},
            {"tag_name": "v7.9.0", "name": "7.9.0", "body": "below the floor"},
        ]
    )
    container = FakeContainer([(0, releases_json, "")])
    with patch(
        "src.main_graph.subgraphs.remediation.changelog.settings"
    ) as mock_settings:
        mock_settings.gh_docker_image = "gh-cli:latest"
        mock_settings.github_token = ""
        result = await fetch_release_notes_page(
            "eslint",
            1,
            "8.0.0",
            "9.0.0",
            "/repo",
            container,
            "node:lts-alpine",
            resolved_repo=("eslint", "eslint"),
        )

    assert result["available"] is True
    assert result["page"] == 1
    assert [r["tag"] for r in result["releases"]] == ["v9.0.0", "v8.5.0"]
    # Only 3 releases returned, well under per_page=100 -- no reason to
    # believe another page exists.
    assert result["has_more"] is False
    assert len(container.calls) == 1
    assert "per_page=100&page=1" in container.calls[0]["command"]
    assert "--paginate" not in container.calls[0]["command"]


@pytest.mark.asyncio
async def test_fetch_release_notes_page_has_more_when_page_full_and_above_floor():
    releases = [
        {"tag_name": f"v9.{i}.0", "name": f"9.{i}.0", "body": ""}
        for i in range(100, 0, -1)
    ]  # 100 releases, all above the 8.0.0 floor -- oldest is v9.1.0
    container = FakeContainer([(0, json.dumps(releases), "")])
    with patch(
        "src.main_graph.subgraphs.remediation.changelog.settings"
    ) as mock_settings:
        mock_settings.gh_docker_image = "gh-cli:latest"
        mock_settings.github_token = ""
        result = await fetch_release_notes_page(
            "eslint",
            1,
            "8.0.0",
            "9.100.0",
            "/repo",
            container,
            "node:lts-alpine",
            resolved_repo=("eslint", "eslint"),
        )

    assert result["has_more"] is True


@pytest.mark.asyncio
async def test_fetch_release_notes_page_no_more_when_page_reaches_floor():
    releases = [
        {"tag_name": "v8.1.0", "name": "8.1.0", "body": ""},
        {"tag_name": "v8.0.0", "name": "8.0.0", "body": ""},
    ]  # oldest tag (v8.0.0) is AT the floor, not above it
    container = FakeContainer([(0, json.dumps(releases), "")])
    with patch(
        "src.main_graph.subgraphs.remediation.changelog.settings"
    ) as mock_settings:
        mock_settings.gh_docker_image = "gh-cli:latest"
        mock_settings.github_token = ""
        result = await fetch_release_notes_page(
            "eslint",
            1,
            "8.0.0",
            "8.1.0",
            "/repo",
            container,
            "node:lts-alpine",
            resolved_repo=("eslint", "eslint"),
        )

    assert result["has_more"] is False


@pytest.mark.asyncio
async def test_fetch_release_notes_page_resolves_repo_when_not_given():
    container = FakeContainer(
        [
            (0, "git+https://github.com/eslint/eslint.git\n", ""),
            (0, "[]", ""),
        ]
    )
    with patch(
        "src.main_graph.subgraphs.remediation.changelog.settings"
    ) as mock_settings:
        mock_settings.gh_docker_image = "gh-cli:latest"
        mock_settings.github_token = ""
        result = await fetch_release_notes_page(
            "eslint", 1, None, None, "/repo", container, "node:lts-alpine"
        )

    assert result["available"] is True
    assert len(container.calls) == 2


@pytest.mark.asyncio
async def test_fetch_release_notes_page_gh_failure_reports_unavailable():
    container = FakeContainer([(1, "", "HTTP 404: Not Found")])
    with patch(
        "src.main_graph.subgraphs.remediation.changelog.settings"
    ) as mock_settings:
        mock_settings.gh_docker_image = "gh-cli:latest"
        mock_settings.github_token = ""
        result = await fetch_release_notes_page(
            "eslint",
            1,
            None,
            None,
            "/repo",
            container,
            "node:lts-alpine",
            resolved_repo=("eslint", "eslint"),
        )

    assert result["available"] is False
    assert "404" in result["error"]
