"""Task 8.7: nothing generates committed output to `out/` any more, and the
served application's exports (task 7.7) are the only way a list leaves it.
Task 8.4's documentation half is pinned here too: the README and `docs/index.md`
must not direct a reader to a file that is no longer the product.

Checked against the tree at commit b04731c (old `src/hotspots.py`, the template
and the five `out/` files present, the old README and `docs/index.md`): fourteen
of these seventeen tests fail there. The three that pass on both trees are guards
rather than changes:
`test_explicit_paths_are_the_only_place_a_run_writes`,
`test_the_served_application_writes_no_file` and
`test_the_only_disk_writers_in_src_take_a_directory_the_caller_names` (they pin
what was already true and must stay true). No test here needs the network or the
mirror: the live pipeline's two network functions are replaced with in-memory
stand-ins.
"""

import ast
import collections
import datetime
import pathlib
import re

import pytest

import hotspots

REPO = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO / "src"


# ----------------------------------------------------------------- the retired path


def test_the_standalone_board_template_is_gone_and_the_served_page_remains():
    assert not (REPO / "web" / "template.html").exists()
    # The served application's own files must not have gone with it.
    assert (REPO / "web" / "app" / "index.html").exists()
    assert (REPO / "web" / "map-network.json").exists()


def test_the_out_directory_and_its_five_files_are_deleted():
    """The committed snapshots were deleted, not merely left alone: no file under
    `out/`, and not the directory either. They remain readable in commit b04731c."""
    assert not (REPO / "out").exists()
    for name in ("blocks.csv", "blocks.md", "triage-board.html",
                 "watchlist.csv", "watchlist.md"):
        assert not (REPO / "out" / name).exists(), name


def test_hotspots_no_longer_exposes_a_board_writer():
    assert not hasattr(hotspots, "write_board")
    # What `src/mirror/derive.py` and the reconciliation still import must survive.
    for name in ("load", "build", "roll_blocks", "clean_address", "to_local",
                 "fetch_blocks", "find_block", "in_block", "write_csv",
                 "write_brief", "write_block_brief", "vehicle_key", "street_of",
                 "HALIFAX", "query", "main"):
        assert hasattr(hotspots, name), f"hotspots.{name} is imported elsewhere"


def _two_recent_calls():
    """Two calls at one address, ten and five days before a fixed 'latest', so
    `build()` lists it (min_calls defaults to 2) with no census block needed."""
    latest = datetime.datetime(2026, 6, 1, 12, tzinfo=datetime.UTC)
    calls = [
        {"REQUEST_ID": i, "ADDRESS": "1 SAME ST, HALIFAX", "COMMUNITY": "HALIFAX",
         "DISTRICT": 7, "RESOLUTION": "Requested Service Provided",
         "LATITUDE": 44.65, "LONGITUDE": -63.57, "INITIATED_BY": "INTERNAL",
         "DATE_CLOSED": None,
         "DATE_INITIATED": int((latest - datetime.timedelta(days=d)).timestamp() * 1000)}
        for i, d in ((1, 10), (2, 5))
    ]
    fields = collections.defaultdict(dict)
    fields[1] = {"Vehicle Make": "HONDA", "Vehicle Model": "CIVIC", "Vehicle Colour": "RED"}
    fields[2] = {"Vehicle Make": "FORD", "Vehicle Model": "F150", "Vehicle Colour": "BLUE"}
    return calls, fields


@pytest.fixture
def offline_pipeline(monkeypatch):
    calls, fields = _two_recent_calls()
    monkeypatch.setattr(hotspots, "load", lambda violation: (calls, fields))
    monkeypatch.setattr(hotspots, "fetch_blocks", lambda: [])


def _run_main(monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["hotspots.py", *argv])
    return hotspots.main()


def test_a_bare_run_refuses_rather_than_writing_to_out(offline_pipeline, tmp_path,
                                                        monkeypatch):
    """The old `main()` defaulted `--csv`/`--brief`/`--block-csv`/`--block-brief`
    to `out/...`, so a bare run committed a list. Now the location has no
    default. The directory is pre-created so a write there would succeed and be
    seen, rather than failing for want of a directory."""
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch)

    assert exc.value.code != 0
    assert list(out.iterdir()) == []


def test_explicit_paths_are_the_only_place_a_run_writes(offline_pipeline, tmp_path,
                                                         monkeypatch):
    dest = tmp_path / "dest"
    dest.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.chdir(tmp_path)

    code = _run_main(
        monkeypatch,
        "--csv", str(dest / "w.csv"), "--brief", str(dest / "w.md"),
        "--block-csv", str(dest / "b.csv"), "--block-brief", str(dest / "b.md"),
    )

    assert code == 0
    assert (dest / "w.csv").exists() and (dest / "w.md").exists()
    assert list(out.iterdir()) == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["dest", "out"]


def test_a_board_is_not_written_even_when_a_template_is_present(offline_pipeline,
                                                                 tmp_path, monkeypatch):
    """The old code wrote a page (by default into `out/`) whenever
    `web/template.html` existed relative to the cwd, so this puts a template where
    the old code would find it and requires that no page appears anywhere."""
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "template.html").write_text("<html>__DATA__</html>")
    (tmp_path / "web" / "map-network.json").write_text("{}")
    dest = tmp_path / "dest"
    dest.mkdir()
    out = tmp_path / "out"
    out.mkdir()  # so a write to the old default location would succeed and be seen
    monkeypatch.chdir(tmp_path)

    code = _run_main(
        monkeypatch,
        "--csv", str(dest / "w.csv"), "--brief", str(dest / "w.md"),
        "--block-csv", str(dest / "b.csv"), "--block-brief", str(dest / "b.md"),
    )

    assert code == 0
    # The only page anywhere under the cwd is the template this test planted.
    assert [p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*.html")] == [
        "web/template.html"]
    # No census block is loaded here, so only the doorway list and brief are written.
    assert sorted(p.name for p in dest.iterdir()) == ["w.csv", "w.md"]
    assert list(out.iterdir()) == []


def test_the_board_option_is_no_longer_accepted(offline_pipeline, tmp_path, monkeypatch,
                                                capsys):
    """`--board` used to be a hidden no-op kept so `reconcile_figures.run_live`
    would not break. Both ends are gone: argparse now rejects it (exit status 2)
    and names it, and nothing is written."""
    dest = tmp_path / "dest"
    dest.mkdir()
    board = dest / "triage-board.html"
    monkeypatch.chdir(tmp_path)

    with pytest.raises(SystemExit) as exc:
        _run_main(
            monkeypatch,
            "--csv", str(dest / "w.csv"), "--brief", str(dest / "w.md"),
            "--block-csv", str(dest / "b.csv"), "--block-brief", str(dest / "b.md"),
            "--board", str(board),
        )

    assert exc.value.code == 2
    assert "--board" in capsys.readouterr().err
    assert list(dest.iterdir()) == []


def test_the_retired_options_are_gone_from_the_command_line(capsys, monkeypatch):
    monkeypatch.setattr("sys.argv", ["hotspots.py", "--help"])
    with pytest.raises(SystemExit) as exc:
        hotspots.main()
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "--template" not in help_text
    assert "--network" not in help_text
    assert "out/" not in help_text


# ------------------------------------------------ nothing under src/ names out/ to write


def _python_files():
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def _docstring_nodes(tree):
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                found.add(id(body[0].value))
    return found


OUT_PATH = re.compile(r"^(\./)?out(/|$)\S*$")


def test_no_source_file_names_an_out_path_as_a_value():
    """A string value shaped like a path into `out/` (an argparse default, a
    `Path("out")`, an `open("out/...")`) is how a module would write there. A
    docstring or a sentence about `out/` is not a path and is not matched."""
    offenders = []
    for path in _python_files():
        tree = ast.parse(path.read_text())
        docstrings = _docstring_nodes(tree)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and id(node) not in docstrings and OUT_PATH.match(node.value)):
                offenders.append(f"{path.relative_to(REPO)}:{node.lineno}: {node.value!r}")
    assert offenders == []


def _file_writing_calls(tree):
    """Calls that put bytes on disk: `open(..., "w"/"a"/"x")`, `.write_text`,
    `.write_bytes`, `.mkdir`."""
    calls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", None)
        if name in ("write_text", "write_bytes", "mkdir"):
            calls.append((node.lineno, name))
        elif name == "open":
            mode = None
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                mode = node.args[1].value
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = kw.value.value
            if isinstance(mode, str) and set(mode) & set("wax+"):
                calls.append((node.lineno, "open-for-write"))
    return calls


def test_the_served_application_writes_no_file():
    """A list leaves the application only over HTTP -- through the export routes
    (7.7) or the JSON the page itself reads -- because `src/app/` writes nothing
    to disk."""
    for path in (SRC / "app").rglob("*.py"):
        assert _file_writing_calls(ast.parse(path.read_text())) == [], path


def test_the_only_disk_writers_in_src_take_a_directory_the_caller_names():
    """Pins the survey behind 8.7's verify clause: outside `src/app/`, only
    `hotspots.py` (its `write_*` helpers), `mirror/derive.py` (its CLI, through
    those helpers) and `mirror/reconcile_figures.py` (which makes the scratch
    directory it runs those two into, a temp directory unless `--out-dir` names
    one) put anything on disk, and none of them has a default inside the
    repository. A new writer has to be added to this list on purpose."""
    writers = {
        str(p.relative_to(SRC))
        for p in _python_files()
        if _file_writing_calls(ast.parse(p.read_text()))
    }
    assert writers == {"hotspots.py", "mirror/derive.py", "mirror/reconcile_figures.py"}


# ------------------------------------------------------------------ 8.4: the documents


README = (REPO / "README.md").read_text()
DOCS_INDEX = (REPO / "docs" / "index.md").read_text()


def test_readme_no_longer_tells_a_reader_to_open_the_out_file():
    assert "Double-click" not in README and "double-click" not in README
    assert "Open the board" in README
    # The reader is pointed at a URL and at the two export routes.
    assert "http://localhost:8000/" in README
    assert "/api/types/<slug>/export.csv" in README
    assert "/types/<slug>/export" in README
    # Each `out/triage-board.html` mention (if any) must say it is not current.
    for line in README.splitlines():
        if "triage-board.html" in line:
            assert re.search(r"historical|retired|no longer|not the product", line, re.I)


def test_readme_does_not_present_the_out_files_as_output_of_a_run():
    assert "python3 src/hotspots.py\n" not in README  # the bare, out/-writing form
    table = [ln for ln in README.splitlines() if ln.startswith("| `out/")]
    assert table == []


def test_docs_index_no_longer_lists_the_out_board_as_the_board():
    assert "generated by `src/hotspots.py` on every run" not in DOCS_INDEX
    assert "Built from `web/template.html`" not in DOCS_INDEX
    for line in DOCS_INDEX.splitlines():
        if line.startswith("| `out/"):
            assert "Not the board" in line or "retired generator" in line
    assert "web/app/index.html" in DOCS_INDEX


def test_docs_index_records_where_the_retired_template_can_still_be_read():
    assert "b04731c" in DOCS_INDEX
    assert "git show b04731c:web/template.html" in DOCS_INDEX
    assert "b04731c" in README


def _flat(text):
    return " ".join(text.split())


def test_readme_says_the_out_directory_was_removed_and_where_to_read_it():
    flat = _flat(README)
    assert "`out/` directory" in flat and "has been removed" in flat
    assert "b04731c" in flat
    assert "git show b04731c:out/watchlist.csv" in flat
    assert "git show b04731c:web/template.html" in flat
    # The old wording: the directory as something still present.
    assert "directory holds the last lists" not in flat
    assert "historical snapshots" not in flat


def test_docs_index_says_the_out_files_were_deleted_and_where_to_read_them():
    flat = _flat(DOCS_INDEX)
    assert "has been deleted" in flat
    assert "git show b04731c:<path>" in flat
    assert "git show b04731c:web/template.html" in flat
    # The old wording: the files as remaining, kept snapshots.
    assert "These files remain" not in flat
    assert "kept as they were" not in flat
    # Every `out/` file the section names is marked as deleted, not as present.
    assert "| File (deleted) |" in DOCS_INDEX
    for name in ("triage-board.html", "watchlist.csv", "watchlist.md",
                 "blocks.csv", "blocks.md"):
        assert f"`out/{name}`" in DOCS_INDEX
