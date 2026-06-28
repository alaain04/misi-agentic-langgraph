"""Architecture boundary enforcement — static import checks."""

import ast
from pathlib import Path

_SRC = Path(__file__).parents[2] / "src"

_FORBIDDEN_IN_NODES = {
    "src.db.connection",
    "src.utils.trivy",
    "src.main_graph.subgraphs.discovery.dao",
    "src.services.dependencies",
    "src.services.vector_store",
}

_FORBIDDEN_IN_DOMAIN_PORTS = {
    "langgraph",
    "motor",
    "pymongo",
    "src.services",
    "src.db",
}

# Skills are the new first-class investigation units; they must not reference
# the deleted ingestion_subgraphs namespace.
_FORBIDDEN_IN_SKILLS = {
    "src.main_graph.subgraphs.ingestion_subgraphs",
}


def _get_imports(path: Path) -> set[str]:
    source = path.read_text()
    tree = ast.parse(source, filename=str(path))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module)
    return imports


def _node_files():
    patterns = [
        "main_graph/nodes/*.py",
        "main_graph/subgraphs/*/nodes/*.py",
    ]
    files = []
    for pattern in patterns:
        files.extend(_SRC.glob(pattern))
    return [f for f in files if f.name != "__init__.py"]


def test_nodes_do_not_import_concrete_infrastructure():
    """Node files must not import concrete DAOs, get_db, or trivy directly."""
    violations = []
    for node_file in _node_files():
        imports = _get_imports(node_file)
        bad = imports & _FORBIDDEN_IN_NODES
        if bad:
            violations.append(f"{node_file.relative_to(_SRC)}: {bad}")
    assert not violations, "Forbidden imports in node files:\n" + "\n".join(violations)


def test_domain_ports_have_no_infrastructure_imports():
    """domain/ports/*.py must not import infrastructure packages."""
    violations = []
    for port_file in (_SRC / "domain" / "ports").glob("*.py"):
        if port_file.name == "__init__.py":
            continue
        imports = _get_imports(port_file)
        for imp in imports:
            for forbidden in _FORBIDDEN_IN_DOMAIN_PORTS:
                if imp.startswith(forbidden):
                    violations.append(f"{port_file.name}: imports {imp}")
    assert not violations, "Forbidden imports in domain ports:\n" + "\n".join(violations)


_LANGGRAPH_EXEMPT_SERVICES = {
    # investigation_planner_service.py intentionally uses Command, interrupt, END for HITL loop
    "investigation_planner_service.py",
}


def test_service_files_do_not_import_from_langgraph():
    """subgraph service.py files must not import from langgraph (orchestration belongs in nodes)."""
    violations = []
    service_files = list(_SRC.glob("main_graph/**/service.py")) + list(_SRC.glob("main_graph/nodes/*_service.py"))
    for svc_file in service_files:
        if svc_file.name in _LANGGRAPH_EXEMPT_SERVICES:
            continue
        imports = _get_imports(svc_file)
        bad = {i for i in imports if i.startswith("langgraph")}
        if bad:
            violations.append(f"{svc_file.relative_to(_SRC)}: {bad}")
    assert not violations, "LangGraph imports in service files:\n" + "\n".join(violations)


def test_skills_do_not_import_from_deleted_ingestion_subgraphs():
    """Skills must not reference the deleted ingestion_subgraphs namespace."""
    violations = []
    skill_files = [
        f for f in (_SRC / "main_graph" / "skills").rglob("*.py")
        if f.name != "__init__.py"
    ]
    for skill_file in skill_files:
        imports = _get_imports(skill_file)
        for imp in imports:
            for forbidden in _FORBIDDEN_IN_SKILLS:
                if imp.startswith(forbidden):
                    violations.append(f"{skill_file.relative_to(_SRC)}: imports {imp}")
    assert not violations, "Forbidden imports in skill files:\n" + "\n".join(violations)
