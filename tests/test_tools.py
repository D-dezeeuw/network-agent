from unittest.mock import patch

import tools


def test_execute_tool_unknown():
    result = tools.execute_tool("nonexistent_tool", {})
    assert "error" in result
    assert "unknown tool" in result["error"]


def test_execute_tool_routes_to_impl():
    with patch.object(tools, "_get_alarms", return_value=[{"id": 1}]):
        result = tools.execute_tool("get_alarms", {})
    assert result == [{"id": 1}]


def test_execute_tool_passes_arguments():
    with patch.object(tools, "_get_auth_log", return_value={"failed_attempts": 3}) as m:
        result = tools.execute_tool("get_auth_log", {"hours": 6})
    m.assert_called_once_with(hours=6)
    assert result == {"failed_attempts": 3}


def test_execute_tool_handles_bad_arguments():
    result = tools.execute_tool("get_auth_log", {"bogus_kwarg": True})
    assert "error" in result
    assert "bad arguments" in result["error"]


def test_execute_tool_wraps_impl_exceptions():
    with patch.object(tools, "_get_alarms", side_effect=RuntimeError("boom")):
        result = tools.execute_tool("get_alarms", {})
    assert "error" in result
    assert "boom" in result["error"]


def test_tools_schema_matches_impls():
    schema_names = {entry["function"]["name"] for entry in tools.TOOLS_SCHEMA}
    impl_names = set(tools.TOOL_IMPLS.keys())
    assert schema_names == impl_names, (
        f"schema/impl drift: only-in-schema={schema_names - impl_names}, "
        f"only-in-impl={impl_names - schema_names}"
    )
