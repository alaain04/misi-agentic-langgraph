from __future__ import annotations

from src.main_graph.subgraphs.discovery.nodes.clone_repo import _clone_command


def test_clone_command_without_token_has_no_auth_header():
    command, secret_env = _clone_command("https://github.com/x/y", None)

    assert "http.extraHeader" not in command
    assert secret_env is None


def test_clone_command_never_puts_token_value_in_the_command_string():
    command, secret_env = _clone_command("https://github.com/x/y", "ghp_SECRET")

    assert "ghp_SECRET" not in command
    assert secret_env == {"GIT_TOKEN": "ghp_SECRET"}


def test_clone_command_disables_base64_line_wrapping():
    """A realistic GitHub PAT (~90+ chars) base64-encodes to well over 76
    columns. base64's default line wrap inserts a literal newline into the
    encoded Authorization header value; git/curl then reject the header
    with "A libcurl function was given a bad argument" because an embedded
    CR/LF in a header value is invalid. Reproduced live against the actual
    alpine/git image: plain `base64` wraps a 93-char-token header across two
    lines, `base64 -w0` keeps it on one. -w0 must be present so long PATs
    don't break private-repo cloning."""
    command, _ = _clone_command("https://github.com/x/y", "a-token")

    assert "base64 -w0" in command
