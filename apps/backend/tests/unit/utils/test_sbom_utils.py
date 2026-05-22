from src.main_graph.utils.sbom_utils import (
    get_vcs_url,
    get_component_version,
    parse_github_owner_repo,
)

_SBOM = {
    "components": [
        {
            "name": "lodash",
            "version": "4.17.21",
            "externalReferences": [
                {"type": "website", "url": "https://lodash.com"},
                {"type": "vcs", "url": "https://github.com/lodash/lodash"},
            ],
        },
        {
            "name": "react",
            "version": "18.2.0",
            "externalReferences": [],
        },
    ]
}


def test_get_vcs_url_found():
    assert get_vcs_url(_SBOM, "lodash") == "https://github.com/lodash/lodash"


def test_get_vcs_url_not_found():
    assert get_vcs_url(_SBOM, "react") is None


def test_get_vcs_url_missing_dep():
    assert get_vcs_url(_SBOM, "unknown") is None


def test_get_component_version():
    assert get_component_version(_SBOM, "lodash") == "4.17.21"
    assert get_component_version(_SBOM, "unknown") is None


def test_parse_github_owner_repo_https():
    assert parse_github_owner_repo("https://github.com/lodash/lodash") == ("lodash", "lodash")


def test_parse_github_owner_repo_git_suffix():
    assert parse_github_owner_repo("git+https://github.com/expressjs/express.git") == ("expressjs", "express")


def test_parse_github_owner_repo_invalid():
    assert parse_github_owner_repo("https://gitlab.com/foo/bar") is None
