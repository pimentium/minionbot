import os

from minionbot.ready import WriteReadyFile


def test_writes_a_timestamp(tmp_path):
    path = tmp_path / "ready"
    assert WriteReadyFile(str(path)) is True
    assert float(path.read_text()) > 0
    assert [p.name for p in tmp_path.iterdir()] == ["ready"]  # no temporary file left behind


def test_replaces_an_older_file(tmp_path):
    path = tmp_path / "ready"
    path.write_text("old")
    os.utime(path, (1, 1))
    assert WriteReadyFile(str(path)) is True
    assert path.stat().st_mtime > 1000


def test_empty_path_does_nothing():
    assert WriteReadyFile("") is False


def test_unwritable_location_is_reported_not_raised(tmp_path, caplog):
    assert WriteReadyFile(str(tmp_path / "missing-dir" / "ready")) is False
    assert "Could not write the ready file" in caplog.text
