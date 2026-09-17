from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_docling_dependency_and_parser_route_are_removed():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    api_router = (ROOT / "app" / "api" / "router.py").read_text(encoding="utf-8")

    assert "docling" not in requirements.lower()
    assert "knowledge_parser" not in api_router
    assert "internal/knowledge-parser" not in api_router


def test_docling_parser_files_are_not_shipped():
    removed_paths = [
        ROOT / "app" / "routers" / "knowledge_parser.py",
        ROOT / "app" / "services" / "knowledge" / "docling_parser_service.py",
        ROOT / "scripts" / "preload_docling_models.py",
    ]

    assert not any(path.exists() for path in removed_paths)
