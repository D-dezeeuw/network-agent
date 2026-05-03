"""In-memory conversation history per user for follow-up Q&A context.

Kept in a process-local dict — lost on container restart, which is the
right semantics: stale conversations don't survive a redeploy. Persisting
would mean dragging old context across deploys and possibly leaking
across model swaps.

Bounded to the last MAX_TURNS user-assistant pairs per user so memory
stays trivial.
"""

import threading

MAX_TURNS = 4

_lock = threading.Lock()
_buffers: dict[int, list[dict]] = {}


def get_history(user_id: int) -> list[dict]:
    """Return the message list for `user_id` ready to prepend to a new
    chat-completion request. Returns a copy so callers can't mutate
    internal state."""
    if user_id is None:
        return []
    with _lock:
        return list(_buffers.get(user_id, []))


def append_turn(user_id: int, user_msg: str, assistant_msg: str) -> None:
    """Append a (user, assistant) pair to the buffer, trimming to MAX_TURNS."""
    if user_id is None:
        return
    with _lock:
        buf = _buffers.setdefault(user_id, [])
        buf.append({"role": "user", "content": user_msg})
        buf.append({"role": "assistant", "content": assistant_msg})
        # Trim: each "turn" is 2 messages
        max_messages = MAX_TURNS * 2
        if len(buf) > max_messages:
            del buf[: len(buf) - max_messages]


def clear(user_id: int) -> bool:
    """Forget `user_id`'s conversation. Returns True if anything was cleared."""
    if user_id is None:
        return False
    with _lock:
        if user_id in _buffers:
            del _buffers[user_id]
            return True
    return False


def turn_count(user_id: int) -> int:
    """Number of completed user-assistant turns for this user."""
    if user_id is None:
        return 0
    with _lock:
        return len(_buffers.get(user_id, [])) // 2
