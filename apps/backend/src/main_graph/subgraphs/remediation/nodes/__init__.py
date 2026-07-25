# NOTE: intentionally does not re-export `remediate` under its own name here.
# `nodes.remediate` is both this package's submodule and that submodule's
# node function; `from .remediate import remediate` would rebind the
# `remediate` attribute on this package to the function, shadowing the
# submodule reference that pytest's monkeypatch (and any getattr-based
# lookup of `nodes.remediate.<name>`) relies on to patch module-level names.
# Import the node function via the fully-qualified submodule path instead:
# `from src.main_graph.subgraphs.remediation.nodes.remediate import remediate`.
