"""Run Watchtower one-shot to check & update Docker containers.

Wraps `containrrr/watchtower --run-once` via the docker SDK. The agent's
own container is excluded so the bot doesn't pull the rug out from under
the running process — re-deploying the agent itself stays a manual step.
"""

import logging
import re

import docker
from docker.errors import DockerException

WATCHTOWER_IMAGE = "containrrr/watchtower:latest"
DOCKER_API_VERSION = "1.54"
RUN_TIMEOUT_SECS = 600  # 10 min hard cap; protects against hung containers
SELF_CONTAINER_NAME = "network-agent"  # matches docker-compose container_name

log = logging.getLogger("watchtower")


def parse_summary(output: str) -> str:
    """Extract a one-line human summary from watchtower's logrus output.

    Watchtower emits a "Session done" line with `Failed=N Scanned=N
    Updated=N` in unspecified order. Search each field independently so
    we don't depend on emission order.
    """
    failed = re.search(r"Failed=(\d+)", output)
    scanned = re.search(r"Scanned=(\d+)", output)
    updated = re.search(r"Updated=(\d+)", output)
    if scanned and updated:
        return (
            f"Scanned {scanned.group(1)}, "
            f"updated {updated.group(1)}, "
            f"failed {failed.group(1) if failed else '0'}"
        )
    return "Session summary not found"


def run_once(timeout: int = RUN_TIMEOUT_SECS) -> dict:
    """Run watchtower one-shot. Returns success/output/summary dict.

    The agent's own container is added to WATCHTOWER_DISABLE_CONTAINERS
    so a stray update doesn't kill the bot mid-message. Containers are
    detached then waited on so we can enforce a hard timeout and force-
    remove the runner if it overshoots.
    """
    try:
        client = docker.from_env(version=DOCKER_API_VERSION)
    except DockerException as e:
        log.exception("docker.from_env failed")
        return {
            "success": False,
            "output": str(e),
            "summary": "Couldn't connect to Docker",
            "exit_code": None,
        }

    env = {
        "DOCKER_API_VERSION": DOCKER_API_VERSION,
        "WATCHTOWER_DISABLE_CONTAINERS": SELF_CONTAINER_NAME,
    }

    log.info("watchtower run_once (timeout=%ds, exclude=%s)",
             timeout, SELF_CONTAINER_NAME)

    container = None
    try:
        container = client.containers.run(
            image=WATCHTOWER_IMAGE,
            command=["--run-once", "--debug"],
            environment=env,
            volumes={
                "/var/run/docker.sock": {
                    "bind": "/var/run/docker.sock",
                    "mode": "rw",
                },
            },
            detach=True,
            remove=False,  # we remove ourselves so timeouts don't leak
        )
        result = container.wait(timeout=timeout)
        output = container.logs().decode("utf-8", errors="replace")
        exit_code = result.get("StatusCode", -1) if isinstance(result, dict) else -1
        return {
            "success": exit_code == 0,
            "exit_code": exit_code,
            "output": output,
            "summary": parse_summary(output),
        }
    except DockerException as e:
        log.exception("watchtower run failed")
        return {
            "success": False,
            "output": str(e),
            "summary": f"Run failed: {e}",
            "exit_code": None,
        }
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except DockerException:
                log.warning("watchtower cleanup: container already gone")
