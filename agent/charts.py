"""PNG chart rendering for digest attachments and the /chart command.

Uses matplotlib's non-interactive Agg backend so it works inside the
container without a display server. matplotlib is heavy (~60MB), so we
import lazily — paying the cost only when a chart is actually rendered.
"""

import io


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _get_plt():
    """Lazy matplotlib import with the headless backend forced."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    return plt


def _save(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=120)
    plt = _get_plt()
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def render_sparkline(values: list[float], title: str = "") -> bytes:
    """Render a small line chart of `values` as PNG bytes.

    Designed for at-a-glance interpretation in Telegram — single line,
    soft fill, minimal axes.
    """
    plt = _get_plt()
    fig, ax = plt.subplots(figsize=(6, 2.2))
    if values:
        x = list(range(len(values)))
        ax.plot(x, values, color="#4a90e2", linewidth=2)
        ax.fill_between(x, values, min(values), alpha=0.18, color="#4a90e2")
        ax.set_xlim(0, max(0, len(values) - 1))
        latest = values[-1]
        ax.annotate(f"{latest:.1f}", xy=(len(values) - 1, latest),
                    xytext=(4, 0), textcoords="offset points",
                    fontsize=9, color="#222")
    else:
        ax.text(0.5, 0.5, "no data", ha="center", va="center",
                transform=ax.transAxes, color="#888")

    if title:
        ax.set_title(title, fontsize=11, loc="left", color="#222")
    ax.set_xticks([])
    ax.tick_params(axis="y", labelsize=8, colors="#666")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#ccc")

    return _save(fig)


def _container_color(c: dict) -> str:
    """Map container status to a cell background color."""
    if c.get("health") == "unhealthy":
        return "#ff6b6b"
    status = c.get("status")
    if status == "running":
        return "#7ed957"
    if status in ("dead", "restarting"):
        return "#ff6b6b"
    if status == "exited" and (c.get("exit_code") or 0) != 0:
        return "#ff6b6b"
    if status == "exited":
        return "#cccccc"
    return "#f5d77a"  # paused, created, etc.


def _disk_color(usage_pct: float) -> str:
    if usage_pct >= 90:
        return "#ff6b6b"
    if usage_pct >= 75:
        return "#f5d77a"
    return "#7ed957"


def render_status_grid(containers: list[dict], disks: dict) -> bytes:
    """Composite status table — one colored row per container plus one per
    disk mount. Colors: green=ok, yellow=watch, red=problem, grey=stopped.
    """
    plt = _get_plt()

    rows: list[list[str]] = []
    cell_colors: list[list[str]] = []

    for c in containers or []:
        rows.append([
            "🐳 " + str(c.get("name", "?")),
            str(c.get("status", "?")),
            str(c.get("health") or "—"),
            f"restarts: {c.get('restart_count', 0)}",
        ])
        color = _container_color(c)
        cell_colors.append([color, color, color, color])

    for mount, usage in (disks or {}).items():
        try:
            pct = float(usage)
        except (TypeError, ValueError):
            continue
        rows.append([
            "💾 " + str(mount),
            f"{pct:.1f}%",
            "",
            "",
        ])
        color = _disk_color(pct)
        cell_colors.append([color, color, color, color])

    if not rows:
        # Empty placeholder
        fig, ax = plt.subplots(figsize=(6, 1.5))
        ax.text(0.5, 0.5, "no container or disk data",
                ha="center", va="center", transform=ax.transAxes, color="#888")
        ax.axis("off")
        return _save(fig)

    height = max(1.5, 0.45 * len(rows) + 0.6)
    fig, ax = plt.subplots(figsize=(8, height))
    ax.axis("off")
    table = ax.table(
        cellText=rows,
        cellColours=cell_colors,
        colLabels=["target", "state", "health", "restarts/usage"],
        colColours=["#ddd"] * 4,
        loc="center",
        cellLoc="left",
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1.0, 1.3)

    return _save(fig)


def render_history_chart(points: list[tuple], title: str = "",
                         ylabel: str = "") -> bytes:
    """Time-axis line chart for /history <metric>.

    `points` is a list of (datetime, float). Empty input renders a
    "no data" placeholder instead of crashing — Telegram still gets a
    valid PNG and the user gets actionable feedback.
    """
    plt = _get_plt()
    import matplotlib.dates as mdates

    fig, ax = plt.subplots(figsize=(8, 3.2))

    if not points:
        ax.text(0.5, 0.5, "no data", ha="center", va="center",
                transform=ax.transAxes, color="#888")
        ax.axis("off")
        return _save(fig)

    xs = [p[0] for p in points]
    ys = [p[1] for p in points]

    ax.plot(xs, ys, color="#4a90e2", linewidth=2, marker="o", markersize=3)
    ax.fill_between(xs, ys, min(ys), alpha=0.18, color="#4a90e2")

    if title:
        ax.set_title(title, fontsize=11, loc="left", color="#222")
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=9, color="#666")

    span_days = (xs[-1] - xs[0]).days if len(xs) > 1 else 0
    if span_days <= 2:
        loc = mdates.HourLocator(interval=max(1, max(1, len(xs) // 6)))
        fmt = mdates.DateFormatter("%m-%d %H:%M")
    elif span_days <= 14:
        loc = mdates.DayLocator()
        fmt = mdates.DateFormatter("%m-%d")
    else:
        loc = mdates.AutoDateLocator()
        fmt = mdates.ConciseDateFormatter(loc)

    ax.xaxis.set_major_locator(loc)
    ax.xaxis.set_major_formatter(fmt)
    fig.autofmt_xdate(rotation=30)

    ax.tick_params(axis="both", labelsize=8, colors="#666")
    ax.grid(axis="y", linestyle=":", alpha=0.4)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#ccc")

    latest = ys[-1]
    ax.annotate(f"{latest:g}", xy=(xs[-1], latest),
                xytext=(4, 0), textcoords="offset points",
                fontsize=9, color="#222")

    return _save(fig)


def is_png(blob: bytes) -> bool:
    return isinstance(blob, (bytes, bytearray)) and blob[:8] == PNG_SIGNATURE
