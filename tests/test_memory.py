import memory


def _reset():
    """Memory is a process-global dict; isolate each test by clearing it."""
    with memory._lock:
        memory._buffers.clear()


def test_history_empty_for_new_user():
    _reset()
    assert memory.get_history(42) == []


def test_append_turn_persists():
    _reset()
    memory.append_turn(42, "hi", "hello back")
    h = memory.get_history(42)
    assert len(h) == 2
    assert h[0] == {"role": "user", "content": "hi"}
    assert h[1] == {"role": "assistant", "content": "hello back"}


def test_append_turn_truncates_to_max_turns():
    _reset()
    for i in range(memory.MAX_TURNS + 3):
        memory.append_turn(42, f"q{i}", f"a{i}")
    h = memory.get_history(42)
    # Each turn = 2 messages; cap is MAX_TURNS turns
    assert len(h) == memory.MAX_TURNS * 2
    # Oldest preserved should be the (3+1)th turn we wrote, not the first
    assert h[0]["content"] == f"q{3}"


def test_get_history_returns_copy():
    _reset()
    memory.append_turn(42, "x", "y")
    h = memory.get_history(42)
    h.append({"role": "user", "content": "should-not-stick"})
    assert len(memory.get_history(42)) == 2


def test_clear_removes_user():
    _reset()
    memory.append_turn(42, "x", "y")
    assert memory.clear(42) is True
    assert memory.clear(42) is False
    assert memory.get_history(42) == []


def test_per_user_isolation():
    _reset()
    memory.append_turn(1, "u1", "a1")
    memory.append_turn(2, "u2", "a2")
    h1 = memory.get_history(1)
    h2 = memory.get_history(2)
    assert h1[0]["content"] == "u1"
    assert h2[0]["content"] == "u2"
    assert len(h1) == 2 and len(h2) == 2


def test_none_user_id_no_op():
    _reset()
    memory.append_turn(None, "x", "y")
    assert memory.get_history(None) == []
    assert memory.clear(None) is False


def test_turn_count():
    _reset()
    assert memory.turn_count(42) == 0
    memory.append_turn(42, "x", "y")
    assert memory.turn_count(42) == 1
    memory.append_turn(42, "a", "b")
    assert memory.turn_count(42) == 2
