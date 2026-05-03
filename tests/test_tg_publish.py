from tg_publish import html_escape


def test_html_escape_ampersand():
    assert html_escape("a & b") == "a &amp; b"


def test_html_escape_lt_gt():
    assert html_escape("a < b > c") == "a &lt; b &gt; c"


def test_html_escape_combined():
    assert html_escape("<script>x & y</script>") == "&lt;script&gt;x &amp; y&lt;/script&gt;"


def test_html_escape_idempotent_safe_input():
    """Safe input passes through unchanged."""
    assert html_escape("hello world") == "hello world"


def test_html_escape_handles_empty_string():
    assert html_escape("") == ""


def test_html_escape_amp_first_to_avoid_double_escape():
    """If we replaced < before &, &lt; would become &amp;lt;. Order matters."""
    assert html_escape("&<") == "&amp;&lt;"
