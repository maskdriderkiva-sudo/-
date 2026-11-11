import io
import os
import sys
import tarfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gbforge import toolchain  # noqa: E402


def test_platform_mapping():
    assert toolchain.asset_for("Windows", "AMD64")[0] == "gbdk-win64.zip"
    assert toolchain.asset_for("Darwin", "arm64")[0] == "gbdk-macos-arm64.tar.gz"
    assert toolchain.asset_for("Darwin", "x86_64")[0] == "gbdk-macos.tar.gz"
    assert toolchain.asset_for("Linux", "x86_64")[0] == "gbdk-linux64.tar.gz"
    with pytest.raises(toolchain.ToolchainError):
        toolchain.asset_for("Linux", "aarch64")


def _tar(path, names):
    with tarfile.open(path, "w:gz") as t:
        for n in names:
            info = tarfile.TarInfo(n)
            info.size = 1
            t.addfile(info, io.BytesIO(b"x"))


def test_rejects_bad_checksum(tmp_path):
    arc = tmp_path / "fake.tar.gz"
    _tar(arc, ["gbdk/bin/lcc"])
    with pytest.raises(toolchain.ToolchainError, match="チェックサム"):
        toolchain.install(archive=arc, target=tmp_path / "out", system="Linux", machine="x86_64", log=lambda m: None)


def test_rejects_path_traversal(tmp_path):
    arc = tmp_path / "evil.tar.gz"
    _tar(arc, ["gbdk/bin/lcc", "../../escape"])
    with pytest.raises(toolchain.ToolchainError, match="不正なパス"):
        toolchain._safe_extract(arc, tmp_path / "x")
    assert not (tmp_path.parent / "escape").exists()


def test_find_prefers_explicit_home(tmp_path, monkeypatch):
    home = tmp_path / "mygbdk"
    (home / "bin").mkdir(parents=True)
    (home / "bin" / "lcc").write_text("")
    monkeypatch.delenv("GBDK_HOME", raising=False)
    lcc, src = toolchain.find(str(home))
    assert lcc == home / "bin" / "lcc" and src == "プロジェクト設定"
