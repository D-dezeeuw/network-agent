from security_scan import _diff_dict, _diff_list


def test_diff_dict_new_entries():
    cur = {"/etc/cron.d/a": "h1", "/etc/cron.d/b": "h2"}
    base = {"/etc/cron.d/a": "h1"}
    assert _diff_dict(cur, base) == {
        "new": ["/etc/cron.d/b"],
        "modified": [],
        "removed": [],
    }


def test_diff_dict_modified_entries():
    cur = {"/etc/cron.d/a": "CHANGED"}
    base = {"/etc/cron.d/a": "h1"}
    assert _diff_dict(cur, base) == {
        "new": [],
        "modified": ["/etc/cron.d/a"],
        "removed": [],
    }


def test_diff_dict_removed_entries():
    cur = {}
    base = {"/etc/cron.d/a": "h1"}
    assert _diff_dict(cur, base) == {
        "new": [],
        "modified": [],
        "removed": ["/etc/cron.d/a"],
    }


def test_diff_dict_mixed():
    cur = {"a": "1", "b": "CHANGED", "c": "3"}
    base = {"a": "1", "b": "2", "d": "4"}
    assert _diff_dict(cur, base) == {
        "new": ["c"],
        "modified": ["b"],
        "removed": ["d"],
    }


def test_diff_dict_empty():
    assert _diff_dict({}, {}) == {"new": [], "modified": [], "removed": []}


def test_diff_list_new_port():
    assert _diff_list([22, 80, 443], [22, 80]) == {"new": [443], "removed": []}


def test_diff_list_removed_port():
    assert _diff_list([22], [22, 80]) == {"new": [], "removed": [80]}


def test_diff_list_swap():
    assert _diff_list([22, 8080], [22, 80]) == {"new": [8080], "removed": [80]}


def test_diff_list_no_change():
    assert _diff_list([22, 80], [22, 80]) == {"new": [], "removed": []}
