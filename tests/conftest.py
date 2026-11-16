import os
import pytest


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("TFTNET_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("TFTNET_OUT", str(tmp_path / "out"))
    from tftnet.config import get_settings
    return get_settings()
