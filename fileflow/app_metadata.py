from pathlib import Path

APP_NAME = "FileFlow"
SAFETY_POLICY_VERSION = 1


def _read_version() -> str:
    version_file = Path(__file__).parent.parent / "VERSION"
    return version_file.read_text(encoding="utf-8").strip()


APP_VERSION = _read_version()
