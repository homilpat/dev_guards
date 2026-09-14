import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Keep tests away from the real ~/.dev-guard policy and audit log."""
    home = tmp_path / "dev-guard-home"
    monkeypatch.setenv("DEV_GUARD_HOME", str(home))
    monkeypatch.delenv("DEV_GUARD_FAIL_OPEN", raising=False)
    return home
