import ast
from pathlib import Path

FORBIDDEN_IMPORT_ROOTS = {
    "aiogram",
    "alembic",
    "asyncio",
    "asyncpg",
    "crypto_smc",
    "fastapi",
    "httpx",
    "sqlalchemy",
}


def test_smc_core_has_no_infrastructure_imports() -> None:
    package_root = Path("src/smc_core")
    violations: list[str] = []

    for path in package_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported_roots: set[str] = set()
            if isinstance(node, ast.Import):
                imported_roots = {alias.name.partition(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots = {node.module.partition(".")[0]}

            forbidden = imported_roots & FORBIDDEN_IMPORT_ROOTS
            if forbidden:
                violations.append(f"{path}: {sorted(forbidden)}")

    assert violations == []
