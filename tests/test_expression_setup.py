import hashlib
import importlib.util
import io
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


spec = importlib.util.spec_from_file_location(
    "setup_expression_models", Path(__file__).parents[1] / "deploy" / "setup_expression_models.py"
)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


@pytest.fixture
def asset():
    return {
        "file": "test.onnx", "size": 5, "sha256": hashlib.sha256(b"valid").hexdigest(),
        "url": "https://media.githubusercontent.com/opencv/opencv_zoo/test.onnx",
    }


def test_download_verified_before_atomic_install_and_skip(tmp_path, asset, monkeypatch):
    fetch = MagicMock(return_value=io.BytesIO(b"valid"))
    monkeypatch.setattr(setup, "urlopen", fetch)
    setup.install_asset(asset, tmp_path)
    assert (tmp_path / "test.onnx").read_bytes() == b"valid"
    setup.install_asset(asset, tmp_path)
    fetch.assert_called_once_with(asset["url"], timeout=60)
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize("contents", [b"wrong", b"x", b"valid-but-too-large"])
def test_bad_download_removes_part_preserves_previous_file(tmp_path, asset, monkeypatch, contents):
    (tmp_path / "test.onnx").write_bytes(b"old")
    monkeypatch.setattr(setup, "urlopen", lambda *args, **kwargs: io.BytesIO(contents))
    with pytest.raises(ValueError, match="verification failed"):
        setup.install_asset(asset, tmp_path)
    assert (tmp_path / "test.onnx").read_bytes() == b"old"
    assert not list(tmp_path.glob("*.part"))


def test_network_failure_cleans_part(tmp_path, asset, monkeypatch):
    monkeypatch.setattr(setup, "urlopen", MagicMock(side_effect=OSError("offline")))
    with pytest.raises(OSError):
        setup.install_asset(asset, tmp_path)
    assert not list(tmp_path.iterdir())


def test_atomic_replace_failure_cleans_part_and_preserves_old(tmp_path, asset, monkeypatch):
    (tmp_path / "test.onnx").write_bytes(b"old")
    monkeypatch.setattr(setup, "urlopen", lambda *args, **kwargs: io.BytesIO(b"valid"))
    monkeypatch.setattr(setup.os, "replace", MagicMock(side_effect=OSError("read-only destination")))
    with pytest.raises(OSError):
        setup.install_asset(asset, tmp_path)
    assert (tmp_path / "test.onnx").read_bytes() == b"old"
    assert not list(tmp_path.glob("*.part"))


def test_offline_source_and_licenses_first_preserve_other_models(tmp_path, asset, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / asset["file"]).write_bytes(b"valid")
    license_asset = {**asset, "file": "LICENSE.txt"}
    (source / "LICENSE.txt").write_bytes(b"valid")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"licenses": [license_asset], "models": [asset]}))
    models = tmp_path / "models"
    models.mkdir()
    (models / "vosk-marker").write_bytes(b"unchanged")
    fetch = MagicMock(side_effect=AssertionError("offline install must not download"))
    monkeypatch.setattr(setup, "urlopen", fetch)
    original = setup.install_asset
    calls = []

    def tracked(entry, directory, source):
        calls.append(entry["file"])
        return original(entry, directory, source)

    monkeypatch.setattr(setup, "install_asset", tracked)
    setup.install(manifest, models / "expressions", source)
    assert calls == ["LICENSE.txt", "test.onnx"]
    assert (models / "vosk-marker").read_bytes() == b"unchanged"
    fetch.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("file", "../escape"), ("file", r"..\escape"), ("size", 6_000_000),
    ("sha256", "invalid"), ("url", "http://media.githubusercontent.com/model"),
])
def test_manifest_rejects_invalid_assets_before_writing(tmp_path, asset, field, value):
    asset[field] = value
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"licenses": [], "models": [asset]}))
    with pytest.raises(ValueError):
        setup.install(manifest, tmp_path / "not-created")
    assert not (tmp_path / "not-created").exists()
