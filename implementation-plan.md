# Implementation Plan

Six-phase rollout to evolve the network-agent's Telegram UX from a single-message daily digest into an interactive ops assistant. Each phase ships behind a feature branch and merges to `main` once its acceptance criteria are met. Phases are sequenced so each can build on prior work, but any single phase is shippable on its own.

## Conventions

- One branch per phase: `feat/phase-N-<slug>`
- One PR per phase against `main`; merge after acceptance
- Tests added alongside features; the existing suite must remain green
- Operator-visible behavior changes get a note in `.env.example`

---

## Phase 1 — Foundation: discoverability & formatting

**Goal:** make commands discoverable via the `/` menu and replace the wall-of-text digest with skimmable, well-formatted messages.
**Branch:** `feat/phase-1-foundation`

- [x] Add slash-command handlers in `agent/bot.py`: `/status`, `/disks`, `/containers`, `/security`, `/updates`, `/news`, `/help`
- [x] Each command maps to a single existing tool from `agent/tools.py`
- [x] On startup, register the command list with Telegram via `setMyCommands` so the `/` menu auto-populates
- [x] Switch `parse_mode` from `Markdown` to `HTML` in `tg_publish.py` and bot replies
- [x] Update the digest prompt in `ai.py` to emit HTML (`<b>`, `<code>`, `<i>`)
- [x] Restructure the digest into 4 sequential messages: (1) status header, (2) security findings, (3) system health, (4) news & metrics
- [x] Add `send_messages(parts: list[str])` helper in `tg_publish.py` for ordered multi-part sends with a small inter-message delay
- [x] Tests: slash-command routing, HTML escaping helper

**Acceptance:** typing `/` in chat shows the command menu; the daily digest arrives as four distinct messages with proper bold/code formatting.

---

## Phase 2 — Interactivity: buttons & acknowledgements

**Goal:** tap-to-snooze persistent findings; stop showing the same finding daily until it changes.
**Branch:** `feat/phase-2-interactivity`

- [ ] Inline keyboards on each finding: `Snooze 24h`, `Snooze 7d`, `Investigate`
- [ ] New module `agent/acks.py`: load/save acknowledgements at `/state/acks.json` (fingerprint → expiry)
- [ ] Fingerprint findings by stable hash of `(category, key)` (e.g. `("cron_new", "/etc/cron.d/foo")`)
- [ ] Filter active acks out of digest data before passing to AI
- [ ] `CallbackQueryHandler` in `bot.py` to process button taps (records ack, replies confirmation)
- [ ] `/acks` command — list active snoozes with expiry timestamps
- [ ] `/unsnooze <fingerprint>` to revert
- [ ] Tests: ack persistence, expiry pruning, fingerprint stability

**Acceptance:** a snoozed finding does not appear in the next digest until expiry; tapping a snooze button returns a confirmation reply.

---

## Phase 3 — Intelligence: trends & forecasts

**Goal:** add temporal context — comparisons vs yesterday and rate-of-change forecasts.
**Branch:** `feat/phase-3-trends`

- [ ] Snapshot persistence: write each digest's metrics + health to `/state/snapshots/<ISO>.json`
- [ ] Rotation: keep last 30 daily snapshots, prune older
- [ ] New module `agent/trends.py`: deltas vs yesterday for CPU/RAM avg, disk usage per mount, restart counts, pending-update count, listening-port count
- [ ] Trend annotations in digest prompt ("+12% vs yesterday", "↑ 3 new updates")
- [ ] Linear-regression forecast for disk usage: "at current trend, /var/lib/docker fills in ~N days"
- [ ] `/trend <metric>` command: ASCII sparkline + delta for one metric
- [ ] Q&A "show its work" footer — list which tools were called
- [ ] Tests: delta math, snapshot rotation, sparkline output

**Acceptance:** the digest annotates ≥3 metrics with vs-yesterday deltas; `/trend cpu` returns a sparkline and a direction call-out.

---

## Phase 4 — Visual: charts as images

**Goal:** replace text-only metrics with at-a-glance images.
**Branch:** `feat/phase-4-charts`

- [ ] Add `matplotlib>=3.8` to `requirements.txt`
- [ ] New module `agent/charts.py`: `render_sparkline(values, title) -> bytes` returning in-memory PNG
- [ ] Per-metric mini sparkline attached to the metrics digest message
- [ ] Status-grid composite: colored table of containers/disks (`render_status_grid()`)
- [ ] `/chart <metric>` command — live chart on demand
- [ ] Cache rendered chart bytes within a single digest cycle
- [ ] Tests: chart functions return non-empty PNG bytes; signature/size sanity

**Acceptance:** the metrics message includes ≥1 chart image; `/chart cpu` returns a chart in-chat.

---

## Phase 5 — Smarter notifications

**Goal:** less noise, faster reaction to actual problems.
**Branch:** `feat/phase-5-notifications`

- [ ] `TELEGRAM_CRITICAL_CHAT_ID` env var: critical findings route here separately from the routine digest channel
- [ ] `QUIET_HOURS` env var (e.g. `22-7`): suppress non-critical digests during the window
- [ ] Real-time alarm poller: every 60s fetch active Netdata alarms, emit to critical chat on new firings
- [ ] Throttle dedup: same alarm fingerprint sent at most once per 30 minutes
- [ ] `/mute_all <duration>` and `/unmute_all` for emergency silence
- [ ] Tests: time-window logic, alarm dedup, severity routing

**Acceptance:** a fresh CRITICAL Netdata alarm shows up within ~60s in the critical chat; routine alarms remain digest-only.

---

## Phase 6 — Operational polish: config via chat

**Goal:** tune and operate the agent without SSH.
**Branch:** `feat/phase-6-config-chat`

- [ ] `/set <KEY> <VALUE>` — write to `/state/overrides.json`, applied on next run
- [ ] `/unset <KEY>` revert
- [ ] `/config` — print current effective configuration (env + overrides, with overrides marked)
- [ ] `/mute <tool>` / `/unmute <tool>` — per-tool disable for the next N digests
- [ ] `/preview` — dry-run of next digest with current config, sent only to caller
- [ ] Conversation memory: in 1:1 chat keep last 4 turns for follow-up context
- [ ] `/clearmemory` to reset the conversation buffer
- [ ] Tests: override layering precedence, conversation buffer truncation, `/preview` output

**Acceptance:** `/set REPORT_HOUR 9` survives a redeploy and the digest fires at the new time; `/mute docker` causes the docker section to be omitted from the next digest.

---

## Out of scope (this plan)

- Web dashboard outside Telegram
- Multi-host fleet support
- Action execution (restart container, run `apt upgrade`) — requires its own auth design
- Voice / multimodal responses
