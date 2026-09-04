import pytest

from jswarm.workitem.identity import parse, WorkItemIdError
from jswarm.workitem.state import work_dir


def test_tracker_key_is_recognised():
    wid = parse("PS-14")
    assert wid.value == "PS-14" and wid.kind == "tracker-key"


def test_slug_is_recognised():
    wid = parse("add-csv-export")
    assert wid.value == "add-csv-export" and wid.kind == "slug"


@pytest.mark.parametrize("raw", ["", "  ", "Add CSV Export", "PS_14", "-leading", "UPPER"])
def test_rejects_what_is_neither(raw):
    with pytest.raises(WorkItemIdError):
        parse(raw)


def test_error_names_both_forms():
    with pytest.raises(WorkItemIdError) as e:
        parse("Add CSV Export")
    msg = str(e.value)
    assert "PS-14" in msg and "add-csv-export" in msg


def test_work_dir_is_identical_for_both_kinds(tmp_path):
    tracker_dir = work_dir(tmp_path, parse("PS-14"))
    slug_dir = work_dir(tmp_path, parse("add-csv-export"))
    assert tracker_dir == tmp_path / ".jswarm" / "work" / "PS-14"
    assert slug_dir == tmp_path / ".jswarm" / "work" / "add-csv-export"
    assert tracker_dir.parent == slug_dir.parent
