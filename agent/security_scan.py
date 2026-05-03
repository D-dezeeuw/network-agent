import hashlib
import json
import os

from config import HOST_PREFIX, STATE_DIR

BASELINE_PATH = os.path.join(STATE_DIR, "baseline.json")

WATCH_PATHS = {
    "cron": [
        f"{HOST_PREFIX}/etc/cron.d",
        f"{HOST_PREFIX}/etc/cron.hourly",
        f"{HOST_PREFIX}/etc/cron.daily",
        f"{HOST_PREFIX}/etc/cron.weekly",
        f"{HOST_PREFIX}/etc/cron.monthly",
        f"{HOST_PREFIX}/var/spool/cron/crontabs",
        f"{HOST_PREFIX}/var/spool/cron",
    ],
    "systemd": [
        f"{HOST_PREFIX}/etc/systemd/system",
    ],
}

SUSPICIOUS_EXE_PREFIXES = ("/tmp/", "/var/tmp/", "/dev/shm/")


def _hash_file(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def _walk_paths(roots: list[str]) -> dict[str, str]:
    found = {}
    for root in roots:
        if not os.path.exists(root):
            continue
        if os.path.isfile(root):
            h = _hash_file(root)
            if h:
                found[root] = h
            continue
        for dirpath, _, files in os.walk(root):
            for name in files:
                full = os.path.join(dirpath, name)
                h = _hash_file(full)
                if h:
                    found[full] = h
    return found


def _authorized_keys() -> dict[str, str]:
    found = {}
    root_keys = f"{HOST_PREFIX}/root/.ssh/authorized_keys"
    h = _hash_file(root_keys)
    if h:
        found[root_keys] = h

    home_root = f"{HOST_PREFIX}/home"
    if os.path.isdir(home_root):
        for user in os.listdir(home_root):
            ak = os.path.join(home_root, user, ".ssh", "authorized_keys")
            h = _hash_file(ak)
            if h:
                found[ak] = h
    return found


def _read_ld_so_preload() -> str:
    path = f"{HOST_PREFIX}/etc/ld.so.preload"
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def _listening_ports() -> list[int]:
    ports = set()
    for path in [f"{HOST_PREFIX}/proc/net/tcp", f"{HOST_PREFIX}/proc/net/tcp6"]:
        if not os.path.exists(path):
            continue
        try:
            with open(path) as f:
                next(f, None)
                for line in f:
                    parts = line.split()
                    if len(parts) >= 4 and parts[3] == "0A":
                        port_hex = parts[1].split(":")[1]
                        ports.add(int(port_hex, 16))
        except OSError:
            pass
    return sorted(ports)


def _suspicious_processes() -> list[dict]:
    suspicious = []
    proc_root = f"{HOST_PREFIX}/proc"
    if not os.path.isdir(proc_root):
        return suspicious
    for entry in os.listdir(proc_root):
        if not entry.isdigit():
            continue
        exe_link = os.path.join(proc_root, entry, "exe")
        try:
            target = os.readlink(exe_link)
        except OSError:
            continue
        if not (target.startswith(SUSPICIOUS_EXE_PREFIXES) or "(deleted)" in target):
            continue
        cmdline_path = os.path.join(proc_root, entry, "cmdline")
        try:
            with open(cmdline_path, "rb") as f:
                cmdline = f.read().replace(b"\x00", b" ").decode(errors="replace").strip()
        except OSError:
            cmdline = ""
        suspicious.append({"pid": int(entry), "exe": target, "cmdline": cmdline})
    return suspicious


def _take_snapshot() -> dict:
    return {
        "authorized_keys": _authorized_keys(),
        "cron": _walk_paths(WATCH_PATHS["cron"]),
        "systemd": _walk_paths(WATCH_PATHS["systemd"]),
        "ld_so_preload": _read_ld_so_preload(),
        "listening_ports": _listening_ports(),
    }


def _diff_dict(current: dict, baseline: dict) -> dict:
    return {
        "new": sorted(set(current) - set(baseline)),
        "modified": sorted(p for p in (set(current) & set(baseline)) if current[p] != baseline[p]),
        "removed": sorted(set(baseline) - set(current)),
    }


def _diff_list(current: list, baseline: list) -> dict:
    cur, base = set(current), set(baseline)
    return {"new": sorted(cur - base), "removed": sorted(base - cur)}


def run_scan(reset: bool = False) -> dict:
    os.makedirs(STATE_DIR, exist_ok=True)
    current = _take_snapshot()
    suspicious = _suspicious_processes()

    if reset and os.path.exists(BASELINE_PATH):
        os.remove(BASELINE_PATH)

    if not os.path.exists(BASELINE_PATH):
        with open(BASELINE_PATH, "w") as f:
            json.dump(current, f, indent=2, sort_keys=True)
        return {
            "baseline_established": True,
            "ld_so_preload": current["ld_so_preload"],
            "suspicious_processes": suspicious,
            "deltas": {},
        }

    with open(BASELINE_PATH) as f:
        baseline = json.load(f)

    return {
        "baseline_established": False,
        "ld_so_preload": current["ld_so_preload"],
        "ld_so_preload_changed": current["ld_so_preload"] != baseline.get("ld_so_preload", ""),
        "suspicious_processes": suspicious,
        "deltas": {
            "authorized_keys": _diff_dict(current["authorized_keys"], baseline.get("authorized_keys", {})),
            "cron": _diff_dict(current["cron"], baseline.get("cron", {})),
            "systemd": _diff_dict(current["systemd"], baseline.get("systemd", {})),
            "listening_ports": _diff_list(current["listening_ports"], baseline.get("listening_ports", [])),
        },
    }


if __name__ == "__main__":
    import pprint
    pprint.pp(run_scan())
