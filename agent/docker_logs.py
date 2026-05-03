"""Read container logs via the Docker daemon.

Backs the /logs slash command and the Q&A `get_container_logs` tool.
The daemon's default json-file driver already buffers per-container
output, so for "what's this container saying right now?" we don't need
an aggregator (Loki/ELK) — just a thin wrapper around `container.logs()`.

Container resolution: exact name first, then case-insensitive substring
match. Substring is rejected if it's ambiguous so the caller can't
silently get logs for the wrong container.
"""

import logging
from datetime import datetime, timedelta, timezone

log = logging.getLogger("docker_logs")

DEFAULT_TAIL = 100
MAX_TAIL = 500


def _client():
    import docker as dockerlib
    return dockerlib.DockerClient(base_url="unix:///var/run/docker.sock")


def list_container_names() -> list[str]:
    """All container names (running + stopped), sorted. Used to surface
    valid choices in error messages and the /logs usage hint."""
    try:
        client = _client()
        return sorted(c.name for c in client.containers.list(all=True))
    except Exception as e:
        log.warning("list_container_names failed: %s", e)
        return []


def _resolve_container(client, query: str):
    """Return (container, None) on hit, or (None, error_str) on miss/ambiguity.

    Tries exact name first via `containers.get` (also matches container IDs),
    then falls back to case-insensitive substring across all containers.
    """
    try:
        return client.containers.get(query), None
    except Exception:
        pass
    q = query.lower()
    matches = [c for c in client.containers.list(all=True) if q in c.name.lower()]
    if not matches:
        return None, f"no container matches {query!r}"
    if len(matches) > 1:
        names = ", ".join(c.name for c in matches[:5])
        return None, f"ambiguous {query!r} matches: {names}"
    return matches[0], None


def get_container_logs(name: str, tail: int = DEFAULT_TAIL,
                       since_minutes: int | None = None) -> dict:
    """Fetch recent stdout/stderr from a container.

    Returns a dict with `name`, `status`, `lines`, `line_count`, plus the
    resolved tail/since values — or `{"error": ..., "available": [...]}`
    when resolution or fetch fails. Lines are timestamp-prefixed.
    """
    if not name:
        return {"error": "name required"}
    if tail is None:
        tail = DEFAULT_TAIL
    try:
        tail = max(1, min(int(tail), MAX_TAIL))
    except (TypeError, ValueError):
        return {"error": f"tail must be an integer, got {tail!r}"}

    try:
        client = _client()
    except Exception as e:
        return {"error": f"docker client init failed: {e}"}

    container, err = _resolve_container(client, name)
    if err:
        return {"error": err, "available": list_container_names()[:50]}

    kwargs = {"tail": tail, "timestamps": True, "stdout": True, "stderr": True}
    if since_minutes:
        try:
            kwargs["since"] = datetime.now(timezone.utc) - timedelta(minutes=int(since_minutes))
        except (TypeError, ValueError):
            return {"error": f"since_minutes must be an integer, got {since_minutes!r}"}

    try:
        raw = container.logs(**kwargs)
    except Exception as e:
        return {
            "error": f"logs() failed: {e}",
            "name": container.name,
            "status": container.status,
        }

    text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
    lines = text.splitlines()

    return {
        "name": container.name,
        "status": container.status,
        "tail_requested": tail,
        "since_minutes": since_minutes,
        "lines": lines,
        "line_count": len(lines),
    }


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else None
    if not target:
        print("Containers:", list_container_names())
    else:
        result = get_container_logs(target, tail=50)
        if result.get("error"):
            print(f"ERROR: {result['error']}")
        else:
            print(f"--- {result['name']} ({result['status']}) ---")
            for line in result["lines"]:
                print(line)
