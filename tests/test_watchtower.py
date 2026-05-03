from unittest.mock import MagicMock, patch

from docker.errors import DockerException

import watchtower


# --- parse_summary ----------------------------------------------------------

def test_parse_summary_extracts_session_done_in_canonical_order():
    output = (
        'time="2024-01-15T12:00:00Z" level=info msg="Session done" '
        'Failed=0 Scanned=10 Updated=2'
    )
    assert watchtower.parse_summary(output) == "Scanned 10, updated 2, failed 0"


def test_parse_summary_handles_field_order_agnostic():
    """Watchtower doesn't promise field order — be defensive."""
    output = 'msg="Session done" Updated=3 Scanned=15 Failed=1'
    assert watchtower.parse_summary(output) == "Scanned 15, updated 3, failed 1"


def test_parse_summary_returns_default_when_missing():
    assert "not found" in watchtower.parse_summary("nothing useful here")


def test_parse_summary_assumes_zero_failures_when_field_missing():
    """Older watchtower omits Failed=N when there were none."""
    out = 'msg="Session done" Scanned=5 Updated=1'
    summary = watchtower.parse_summary(out)
    assert "5" in summary
    assert "1" in summary
    assert "failed 0" in summary


# --- run_once ---------------------------------------------------------------

def _fake_client_with_logs(stdout: bytes, exit_code: int = 0) -> MagicMock:
    fake_container = MagicMock()
    fake_container.wait.return_value = {"StatusCode": exit_code}
    fake_container.logs.return_value = stdout
    fake_client = MagicMock()
    fake_client.containers.run.return_value = fake_container
    return fake_client


def test_run_once_invokes_correct_image_and_command():
    client = _fake_client_with_logs(
        b'msg="Session done" Failed=0 Scanned=3 Updated=0',
    )
    with patch("watchtower.docker.from_env", return_value=client):
        result = watchtower.run_once()

    args = client.containers.run.call_args.kwargs
    assert args["image"] == watchtower.WATCHTOWER_IMAGE
    assert args["command"] == ["--run-once", "--debug"]
    assert result["success"] is True


def test_run_once_excludes_self_container():
    """The agent must not let watchtower update its own container."""
    client = _fake_client_with_logs(b"")
    with patch("watchtower.docker.from_env", return_value=client):
        watchtower.run_once()

    env = client.containers.run.call_args.kwargs["environment"]
    assert env["WATCHTOWER_DISABLE_CONTAINERS"] == watchtower.SELF_CONTAINER_NAME


def test_run_once_mounts_docker_socket_rw():
    client = _fake_client_with_logs(b"")
    with patch("watchtower.docker.from_env", return_value=client):
        watchtower.run_once()

    volumes = client.containers.run.call_args.kwargs["volumes"]
    sock = volumes["/var/run/docker.sock"]
    assert sock["bind"] == "/var/run/docker.sock"
    assert sock["mode"] == "rw"


def test_run_once_runs_detached_so_we_can_enforce_timeout():
    client = _fake_client_with_logs(b"")
    with patch("watchtower.docker.from_env", return_value=client):
        watchtower.run_once()
    assert client.containers.run.call_args.kwargs["detach"] is True


def test_run_once_passes_timeout_to_wait():
    client = _fake_client_with_logs(b"")
    with patch("watchtower.docker.from_env", return_value=client):
        watchtower.run_once(timeout=42)
    container = client.containers.run.return_value
    container.wait.assert_called_with(timeout=42)


def test_run_once_force_removes_container_after_run():
    client = _fake_client_with_logs(b"")
    with patch("watchtower.docker.from_env", return_value=client):
        watchtower.run_once()
    container = client.containers.run.return_value
    container.remove.assert_called_with(force=True)


def test_run_once_force_removes_even_on_failure():
    """Cleanup must run even when wait() blows up."""
    fake_container = MagicMock()
    fake_container.wait.side_effect = DockerException("timeout")
    fake_client = MagicMock()
    fake_client.containers.run.return_value = fake_container

    with patch("watchtower.docker.from_env", return_value=fake_client):
        result = watchtower.run_once()

    fake_container.remove.assert_called_with(force=True)
    assert result["success"] is False


def test_run_once_handles_docker_unavailable():
    with patch("watchtower.docker.from_env",
               side_effect=DockerException("connection refused")):
        result = watchtower.run_once()
    assert result["success"] is False
    assert "connection refused" in result["output"]
    assert result["exit_code"] is None


def test_run_once_returns_failure_on_non_zero_exit():
    client = _fake_client_with_logs(b"some error", exit_code=1)
    with patch("watchtower.docker.from_env", return_value=client):
        result = watchtower.run_once()
    assert result["success"] is False
    assert result["exit_code"] == 1


def test_run_once_returns_summary_extracted_from_output():
    out = b'msg="Session done" Failed=2 Scanned=7 Updated=1'
    client = _fake_client_with_logs(out)
    with patch("watchtower.docker.from_env", return_value=client):
        result = watchtower.run_once()
    assert "Scanned 7" in result["summary"]
    assert "updated 1" in result["summary"]
    assert "failed 2" in result["summary"]


def test_run_once_swallows_cleanup_failure():
    """If the runner's already gone (auto-removed), don't surface a 2nd error."""
    fake_container = MagicMock()
    fake_container.wait.return_value = {"StatusCode": 0}
    fake_container.logs.return_value = b""
    fake_container.remove.side_effect = DockerException("no such container")
    fake_client = MagicMock()
    fake_client.containers.run.return_value = fake_container

    with patch("watchtower.docker.from_env", return_value=fake_client):
        result = watchtower.run_once()
    # Primary result still reflects the run, not the cleanup failure
    assert result["success"] is True
