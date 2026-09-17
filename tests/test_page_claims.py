"""Static checks on `web/app/index.html`, the served page (task 5.1).

These do not spin up a browser -- there is no browser automation in this
suite -- so they cannot prove a pixel renders a given colour or that a
`matchMedia` handler actually fires. What they can prove, and what would
regress silently otherwise, is that the markup and script text a browser
would load still carries the specific strings two tasks depend on:

* 7.6 -- a reader looking at one doorway row can tell, before opening it,
  that a block with several still-calling doorways is a block-level problem
  rather than a single bad address.
* 5.6 -- light and dark rendering stay defined for the same set of tokens,
  and the canvas map (which paints itself once per `draw()` call rather than
  via CSS) has a handler that redraws it when the OS colour-scheme preference
  changes while the page is open.
"""

import pathlib
import re

PAGE = (pathlib.Path(__file__).parent.parent / "web" / "app" / "index.html").read_text()


# --------------------------------------------------------------------- 7.6


def test_neighbour_count_column_explains_itself():
    """The doorway table's "On block" header carries a tooltip explaining what
    the count means and what it implies for scoping a remedy, so a reader does
    not have to open a row to learn how to read it."""
    header = re.search(r'<div class="num"[^>]*>On block</div>', PAGE)
    assert header, "the doorway table's 'On block' header is missing"
    assert 'title="' in header.group(0), "the 'On block' header has no explanatory tooltip"
    assert "block" in header.group(0).lower()


def test_neighbour_flag_marks_block_problem_rows():
    """A doorway row with more than one still-calling doorway on its block is
    visually flagged (the .flag class, used nowhere else for a plain count) and
    carries a title naming it a block-level problem."""
    assert '<span class="flag">${it.nb}</span>' in PAGE
    assert "a block-level problem, not just this address" in PAGE


def test_evidence_text_names_block_scope_before_remedy_buttons():
    """`evidenceDoor()` states the block framing, and that text is placed ahead
    of the triage action buttons in the detail panel, so a reader sees it
    before choosing a remedy for that one address."""
    m = re.search(r"function evidenceDoor\(r\)\{(.*?)\n\}", PAGE, re.S)
    assert m, "evidenceDoor() not found"
    body = m.group(1)
    assert "doorways on this block are still calling" in body
    assert "a sign here alone probably will not settle it" in body
    assert "on its own" in body  # the nb == 1 case names the alternative explicitly

    detail_fn = re.search(r"function detail\(it, s, V\)\{(.*?)\n\}", PAGE, re.S)
    assert detail_fn, "detail() not found"
    detail_body = detail_fn.group(1)
    ev_pos = detail_body.index("evidenceDoor(it)")
    acts_pos = detail_body.index('class="acts"')
    assert ev_pos < acts_pos, "evidence text must render before the remedy action buttons"


# --------------------------------------------------------------------- 5.6


def test_light_and_dark_define_the_same_tokens():
    """Both the system-preference block and the explicit dark override define
    the same custom properties as :root, so nothing falls back to a
    light-only value when the scheme is dark."""
    root_block = re.search(r":root\{(.*?)\}", PAGE, re.S).group(1)
    dark_media_block = re.search(
        r'@media \(prefers-color-scheme:dark\)\{:root:not\(\[data-theme="light"\]\)\{(.*?)\}\}',
        PAGE, re.S,
    ).group(1)
    dark_attr_block = re.search(r':root\[data-theme="dark"\]\{(.*?)\}', PAGE, re.S).group(1)

    token = re.compile(r"--[a-z0-9-]+(?=:)")
    root_tokens = set(token.findall(root_block))
    assert root_tokens, "no custom properties found on :root"
    assert token.findall(dark_media_block) and set(token.findall(dark_media_block)) == root_tokens
    assert token.findall(dark_attr_block) and set(token.findall(dark_attr_block)) == root_tokens


def test_color_scheme_meta_present():
    """`color-scheme: light dark` lets native form controls and scrollbars
    follow the browser's own choice, not just the page's own CSS."""
    assert re.search(r"html\{color-scheme:\s*light\s+dark\}", PAGE)


def test_canvas_redraws_on_live_scheme_change():
    """The map is a canvas, not CSS -- it only repaints when `draw()` runs.
    A listener on the media query's change event is what makes it follow a
    preference flip while the page stays open, per 5.6."""
    assert re.search(
        r'matchMedia\("\(prefers-color-scheme:\s*dark\)"\)\.addEventListener\("change",\s*\(\)\s*=>\s*draw\(\)\)',
        PAGE,
    ), "no listener redraws the canvas map when the OS colour-scheme preference changes"


def test_draw_reads_theme_colours_live_rather_than_caching_them():
    """`draw()` must re-read every CSS custom property it paints with on each
    call (via CSSVAR/getComputedStyle) rather than capturing them once at
    script load, or a live scheme change would leave stale colours on the
    canvas even though the listener fires."""
    m = re.search(r"function draw\(\)\{(.*?)\n\}\n\n/\* ---------- hit testing", PAGE, re.S)
    assert m, "draw() not found"
    body = m.group(1)
    for var in ("--ink", "--line-2", "--muted", "--surface-2", "--surface",
                "--hazard", "--alert", "--accent"):
        assert f'CSSVAR("{var}")' in body, f"draw() does not read {var} live"
    # The status-colour lookup used for markers and block fills must be the
    # live CSS-variable-backed one, not a module-level hex table that would
    # not track a theme change (see 5.6 and the legend at .legend .dot).
    assert "STAT_COLOR[st(" in body
    assert not re.search(r'const COLOR = \{[^}]*#', PAGE), (
        "a hardcoded hex colour table for triage status would not follow "
        "a live colour-scheme change the way the legend's CSS variables do"
    )
