from unittest.mock import MagicMock

import docker_logs


class _FakeContainer:
    def __init__(self, name, status="running", logs_bytes=b"", raises=None):
        self.name = name
        self.status = status
        self._logs_bytes = logs_bytes
        self._raises = raises
        self.logs = MagicMock(side_effect=self._logs_call)

    def _logs_call(self, **kwargs):
        if self._raises:
            raise self._raises
        return self._logs_bytes


def _fake_client(containers, get_raises=True):
    """Build a stand-in for docker.DockerClient.

    When `get_raises` is True, `containers.get(name)` always raises so
    resolution falls through to substring matching — this is the common
    test path.
    """
    client = MagicMock()
    if get_raises:
        client.containers.get.side_effect = Exception("not found")
    else:
        # Return the first container whose name == query
        client.containers.get.side_effect = lambda q: next(
            (c for c in containers if c.name == q),
            (_ for _ in ()).throw(Exception("not found"))
        )
    client.containers.list.return_value = containers
    return client


def test_resolve_container_exact_match(monkeypatch):
    plex = _FakeContainer("plex")
    other = _FakeContainer("nginx")
    client = _fake_client([plex, other], get_raises=False)
    found, err = docker_logs._resolve_container(client, "plex")
    assert err is None
    assert found.name == "plex"


def test_resolve_container_substring_match(monkeypatch):
    target = _FakeContainer("home-plex-1")
    other = _FakeContainer("nginx")
    client = _fake_client([target, other])
    found, err = docker_logs._resolve_container(client, "plex")
    assert err is None
    assert found.name == "home-plex-1"


def test_resolve_container_substring_is_case_insensitive():
    target = _FakeContainer("HomePlex")
    client = _fake_client([target])
    found, err = docker_logs._resolve_container(client, "PLEX")
    assert err is None
    assert found.name == "HomePlex"


def test_resolve_container_no_match():
    client = _fake_client([_FakeContainer("nginx")])
    found, err = docker_logs._resolve_container(client, "plex")
    assert found is None
    assert "no container matches" in err


def test_resolve_container_ambiguous_substring():
    a = _FakeContainer("plex-server")
    b = _FakeContainer("plex-backup")
    client = _fake_client([a, b])
    found, err = docker_logs._resolve_container(client, "plex")
    assert found is None
    assert "ambiguous" in err
    assert "plex-server" in err
    assert "plex-backup" in err


def test_get_container_logs_happy_path(monkeypatch):
    log_bytes = b"2026-05-03T10:00:00Z line one\n2026-05-03T10:00:01Z line two\n"
    plex = _FakeContainer("plex", "running", logs_bytes=log_bytes)
    client = _fake_client([plex], get_raises=False)
    monkeypatch.setattr(docker_logs, "_client", lambda: client)

    result = docker_logs.get_container_logs("plex", tail=50)
    assert "error" not in result
    assert result["name"] == "plex"
    assert result["status"] == "running"
    assert result["line_count"] == 2
    assert result["lines"] == [
        "2026-05-03T10:00:00Z line one",
        "2026-05-03T10:00:01Z line two",
    ]
    plex.logs.assert_called_once()
    kwargs = plex.logs.call_args.kwargs
    assert kwargs["tail"] == 50
    assert kwargs["timestamps"] is True
    assert kwargs["stdout"] is True
    assert kwargs["stderr"] is True


def test_get_container_logs_empty_name():
    assert docker_logs.get_container_logs("").get("error") == "name required"


def test_get_container_logs_caps_tail(monkeypatch):
    plex = _FakeContainer("plex", logs_bytes=b"")
    client = _fake_client([plex], get_raises=False)
    monkeypatch.setattr(docker_logs, "_client", lambda: client)

    docker_logs.get_container_logs("plex", tail=99999)
    assert plex.logs.call_args.kwargs["tail"] == docker_logs.MAX_TAIL


def test_get_container_logs_floors_tail(monkeypatch):
    plex = _FakeContainer("plex", logs_bytes=b"")
    client = _fake_client([plex], get_raises=False)
    monkeypatch.setattr(docker_logs, "_client", lambda: client)

    docker_logs.get_container_logs("plex", tail=0)
    assert plex.logs.call_args.kwargs["tail"] == 1


def test_get_container_logs_invalid_tail_returns_error():
    result = docker_logs.get_container_logs("plex", tail="not-a-number")
    assert "tail must be an integer" in result["error"]


def test_get_container_logs_passes_since(monkeypatch):
    plex = _FakeContainer("plex", logs_bytes=b"")
    client = _fake_client([plex], get_raises=False)
    monkeypatch.setattr(docker_logs, "_client", lambda: client)

    docker_logs.get_container_logs("plex", tail=10, since_minutes=30)
    assert "since" in plex.logs.call_args.kwargs


def test_get_container_logs_no_match_returns_error_and_available(monkeypatch):
    nginx = _FakeContainer("nginx")
    client = _fake_client([nginx])
    monkeypatch.setattr(docker_logs, "_client", lambda: client)
    # list_container_names() builds its own client — also patch it
    monkeypatch.setattr(docker_logs, "list_container_names", lambda: ["nginx"])

    result = docker_logs.get_container_logs("plex", tail=10)
    assert "error" in result
    assert "no container matches" in result["error"]
    assert result["available"] == ["nginx"]


def test_get_container_logs_handles_logs_exception(monkeypatch):
    boom = _FakeContainer("plex", raises=RuntimeError("docker daemon ate it"))
    client = _fake_client([boom], get_raises=False)
    monkeypatch.setattr(docker_logs, "_client", lambda: client)

    result = docker_logs.get_container_logs("plex", tail=10)
    assert "error" in result
    assert "docker daemon ate it" in result["error"]
    # Container was resolved so name/status should still be present.
    assert result["name"] == "plex"


def test_get_container_logs_decodes_invalid_utf8(monkeypatch):
    # Invalid byte sequence should not crash; replaced char emerges instead.
    plex = _FakeContainer("plex", logs_bytes=b"good line\n\xff\xfe broken\n")
    client = _fake_client([plex], get_raises=False)
    monkeypatch.setattr(docker_logs, "_client", lambda: client)

    result = docker_logs.get_container_logs("plex", tail=10)
    assert result["line_count"] == 2
    assert "good line" in result["lines"][0]


def test_get_container_logs_client_init_failure(monkeypatch):
    def _boom():
        raise RuntimeError("socket missing")
    monkeypatch.setattr(docker_logs, "_client", _boom)
    result = docker_logs.get_container_logs("plex")
    assert "docker client init failed" in result["error"]
