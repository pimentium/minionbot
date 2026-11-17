import json

from minionbot.version import DescribeVersion
from tests.test_roles import team  # noqa: F401 (fixture)


def test_no_file_means_development(tmp_path):
    assert DescribeVersion(tmp_path / "none.json") == "development (no version file)"


def test_broken_file_means_development(tmp_path):
    path = tmp_path / "v.json"
    path.write_text("not json")
    assert DescribeVersion(path) == "development (no version file)"
    path.write_text("[]")
    assert DescribeVersion(path) == "development (no version file)"


def test_commit_and_date(tmp_path):
    path = tmp_path / "v.json"
    path.write_text(json.dumps({"commit": "46f992e29bf670f8", "date": "2026-11-17T01:03:00+00:00"}))
    text = DescribeVersion(path)
    assert text.startswith("46f992e, 2026-11-17 ")


def test_commit_without_valid_date(tmp_path):
    path = tmp_path / "v.json"
    path.write_text(json.dumps({"commit": "46f992e29bf670f8", "date": "garbage"}))
    assert DescribeVersion(path) == "46f992e"


def test_version_command_for_admin_only(registry, team):
    from tests.conftest import OWNER_ID
    from tests.test_roles import ADMIN_ID, USER_ID, Run

    assert Run(registry, team, "/version", ADMIN_ID).startswith("Version: ")
    assert Run(registry, team, "/version", OWNER_ID).startswith("Version: ")
    assert "don't have permission" in Run(registry, team, "/version", USER_ID)
