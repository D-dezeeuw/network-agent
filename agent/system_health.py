import os
import subprocess
from datetime import datetime, timezone

NOTABLE_KERNEL_KEYWORDS = (
    "oom", "killed", "error", "fail", "i/o error",
    "ata", "scsi", "segfault", "panic",
)


def check_reboot_required() -> dict:
    flag = "/host/run/reboot-required"
    pkgs_file = "/host/run/reboot-required.pkgs"
    if not os.path.exists(flag):
        return {"required": False, "packages": []}
    pkgs = []
    try:
        with open(pkgs_file) as f:
            pkgs = [l.strip() for l in f if l.strip()]
    except OSError:
        pass
    return {"required": True, "packages": pkgs}


def _parse_apt_output(stdout: str) -> dict:
    lines = [l for l in stdout.splitlines() if "/" in l and "[upgradable" in l]

    def is_security(line: str) -> bool:
        try:
            suite_block = line.split()[0].split("/")[1]
            return "-security" in suite_block
        except IndexError:
            return False

    security_lines = [l for l in lines if is_security(l)]
    return {
        "total": len(lines),
        "security": len(security_lines),
        "security_packages": [l.split("/")[0] for l in security_lines[:15]],
        "all_sample": [l.split("/")[0] for l in lines[:10]],
    }


def check_pending_updates() -> dict:
    cmd = [
        "apt", "list", "--upgradable",
        "-o", "Dir::State=/host/var/lib/apt",
        "-o", "Dir::State::status=/host/var/lib/dpkg/status",
        "-o", "Dir::Cache=/host/var/cache/apt",
        "-o", "Dir::Etc=/host/etc/apt",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    except Exception as e:
        return {"error": str(e), "total": 0, "security": 0}
    return _parse_apt_output(r.stdout)


def _journal_query(args: list[str]) -> list[str]:
    try:
        r = subprocess.run(
            ["journalctl", "--no-pager", *args],
            capture_output=True, text=True, timeout=20,
        )
        return r.stdout.splitlines()
    except Exception as e:
        return [f"[error] {e}"]


def check_journal_errors(hours: int = 24) -> dict:
    lines = _journal_query(["--since", f"{hours} hours ago", "-p", "err", "-o", "short"])
    return {"count": len(lines), "sample": lines[-15:]}


def _filter_notable_kernel(lines: list[str]) -> list[str]:
    return [l for l in lines if any(kw in l.lower() for kw in NOTABLE_KERNEL_KEYWORDS)]


def check_kernel_messages(hours: int = 24) -> dict:
    lines = _journal_query(["-k", "--since", f"{hours} hours ago", "-p", "warning", "-o", "short"])
    notable = _filter_notable_kernel(lines)
    return {"total": len(lines), "notable": len(notable), "sample": notable[-10:]}


def _parse_iso(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_concerning_container(state: dict, health: str | None) -> bool:
    """A container is 'concerning' if it's actively failing — not just stopped.

    Concerning: unhealthy health check, dead, restart-looping, or exited
    with a non-zero exit code (i.e. crashed).

    NOT concerning: clean exited containers (exit 0, common for one-shot
    Docker tasks), paused, created-but-not-yet-started.
    """
    if health == "unhealthy":
        return True
    status = state.get("Status")
    if status in ("dead", "restarting"):
        return True
    if status == "exited" and state.get("ExitCode", 0) != 0:
        return True
    return False


def check_docker_containers() -> dict:
    try:
        import docker as dockerlib
        client = dockerlib.DockerClient(base_url="unix:///var/run/docker.sock")
        containers = client.containers.list(all=True)
    except Exception as e:
        return {"error": str(e)}

    now = datetime.now(timezone.utc)
    results = []
    for c in containers:
        try:
            attrs = c.attrs
            state = attrs.get("State", {})
            health = state.get("Health", {}).get("Status")
            created_dt = _parse_iso(c.image.attrs.get("Created", ""))
            age_days = (now - created_dt).days if created_dt else None
            results.append({
                "name": c.name,
                "status": c.status,
                "exit_code": state.get("ExitCode"),
                "restart_count": attrs.get("RestartCount", 0),
                "image": (c.image.tags or [c.image.id[:19]])[0],
                "image_age_days": age_days,
                "health": health,
                "_concerning": _is_concerning_container(state, health),
            })
        except Exception:
            continue

    concerning = [
        {k: v for k, v in r.items() if not k.startswith("_")}
        for r in results if r["_concerning"]
    ]
    high_restart = [
        {k: v for k, v in r.items() if not k.startswith("_")}
        for r in results if r["restart_count"] > 3
    ]
    stale_images = [
        {"name": r["name"], "image": r["image"], "age_days": r["image_age_days"]}
        for r in results
        if r["image_age_days"] is not None and r["image_age_days"] > 90
    ]
    all_containers = [{k: v for k, v in r.items() if not k.startswith("_")} for r in results]

    return {
        "total": len(results),
        "running": sum(1 for r in results if r["status"] == "running"),
        "concerning": concerning,
        "high_restart": high_restart,
        "stale_images_90d": stale_images,
        "all_containers": all_containers,
    }


def run_health_check() -> dict:
    return {
        "reboot_required": check_reboot_required(),
        "pending_updates": check_pending_updates(),
        "journal_errors_24h": check_journal_errors(),
        "kernel_messages_24h": check_kernel_messages(),
        "docker_containers": check_docker_containers(),
    }


if __name__ == "__main__":
    import pprint
    pprint.pp(run_health_check())
