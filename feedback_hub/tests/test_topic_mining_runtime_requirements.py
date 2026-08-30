from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[2]


def _requirements(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_main_api_runtime_installs_topic_xlsx_dependency():
    base = _requirements(PROJECT_ROOT / "requirements.txt")
    topic = _requirements(PROJECT_ROOT / "requirements-topic-mining.txt")

    assert base.count("openpyxl==3.1.5") == 1
    assert topic == ["-r requirements.txt"]
