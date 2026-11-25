"""Six gallery features (2026-11-21): shared filter, self-filling boards, vision neighbours, /saved, /today."""
import json
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))
import inspi_saved as S  # noqa: E402
import inspi_vision as VI  # noqa: E402
import inspilib as L  # noqa: E402
from test_inspi import make_note  # noqa: E402

CASES = json.loads((HERE / "js" / "inspi_filter_cases.json").read_text())


@pytest.mark.parametrize("case", CASES["searches"], ids=lambda c: json.dumps(c["s"]))
def test_python_filter_matches_fixture(case):
    got = [it["i"] for it in CASES["items"] if S.saved_ok(it, case["s"])]
    assert got == case["want"]


@pytest.mark.skipif(not shutil.which("node"), reason="node absent")
def test_js_filter_matches_same_fixture():
    r = subprocess.run(["node", str(HERE / "js" / "inspi_filter_cases.test.js")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_clean_refuses_bad_values():
    with pytest.raises(ValueError):
        S.clean({"name": "!!!"})
    with pytest.raises(ValueError):
        S.clean({"name": "ok", "color": "red"})
    with pytest.raises(ValueError):
        S.clean({"name": "ok", "tol": 500})
    c = S.clean({"name": "Bleu Calme", "color": "#1E66D0", "facets": {"ty": ["x"], "zz": ["y"]}})
    assert c["name"] == "bleu-calme" and c["color"] == "#1e66d0" and c["facets"] == {"ty": ["x"]}


def test_vision_neighbours_order():
    import numpy as np

    a = np.zeros(VI.DIM, dtype=np.float32); a[0] = 1
    b = np.zeros(VI.DIM, dtype=np.float32); b[0] = 0.9; b[1] = 0.1
    c = np.zeros(VI.DIM, dtype=np.float32); c[2] = 1
    nb = VI.neighbours([a, None, b, c], k=2)
    assert nb[1] == [] and nb[0][0][0] == 2 and nb[0][0][1] > nb[0][1][1]


@pytest.fixture
def vault(tmp_path):
    for k, col in enumerate([(30, 100, 210), (31, 101, 211), (200, 30, 30)]):
        make_note(tmp_path, f"img{k}", ["type: image", f'main_color: "#{col[0]:02x}{col[1]:02x}{col[2]:02x}"'], color=col, size=(60 + k, 40))
    (tmp_path / ".wiki-inspi").mkdir()
    return tmp_path


def items_for(vault):
    notes = L.load_notes(vault)
    items = [{"i": i, "r": n["rel"], "t": n["title"], "b": "", "s": [], "m": [], "fa": {}, "f": 0,
              "c": n["fm"].get("main_color", ""), "pal": n["colors"], "sha": VI.sha1(n["image"])} for i, n in enumerate(notes)]
    return items, {n["rel"]: n for n in notes}


def test_auto_board_created_then_only_new_never_readded(vault, tmp_path, monkeypatch):
    import inspi_board_add

    monkeypatch.setattr(inspi_board_add, "BACKUP", tmp_path / "board-backups")
    S.put(vault, {"name": "bleus", "color": "#1e64d2", "tol": 10, "board": True})
    items, by_rel = items_for(vault)
    rep = S.fill_boards(vault, items, {}, by_rel)
    board = vault / "Moodboards" / "auto-bleus.excalidraw.md"
    assert board.exists() and rep == ["auto-bleus: +2"]
    # nothing new -> nothing written
    assert S.fill_boards(vault, items, {}, by_rel) == []
    # a new blue picture while the board was just edited (Excalidraw may hold it): left alone
    make_note(vault, "img3", ["type: image", 'main_color: "#1e64d0"'], color=(30, 100, 208), size=(90, 40))
    items, by_rel = items_for(vault)
    assert S.fill_boards(vault, items, {}, by_rel) == ["auto-bleus: skipped, edited less than 2 minutes ago"]
    # once the board is old enough, only the new picture is appended
    import os
    old = time.time() - 600
    os.utime(board, (old, old))
    assert S.fill_boards(vault, items, {}, by_rel) == ["auto-bleus: +1"]
    ledger = json.loads((vault / ".wiki-inspi" / "saved-ledger.json").read_text())
    assert len(ledger["auto-bleus"]) == 3


def test_manual_board_never_touched(vault):
    L.write_board(vault, "bleus", L.load_notes(vault)[:1])
    before = (vault / "Moodboards" / "bleus.excalidraw.md").read_text()
    S.put(vault, {"name": "bleus", "color": "#1e64d2", "tol": 10, "board": True})
    items, by_rel = items_for(vault)
    S.fill_boards(vault, items, {}, by_rel)
    assert (vault / "Moodboards" / "bleus.excalidraw.md").read_text() == before


@pytest.fixture
def server(vault, monkeypatch):
    import inspi_map as M
    import inspi_serve as SV

    monkeypatch.setattr(VI, "fill", lambda *a, **k: 0)
    M.build(vault)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), SV.H)
    port = srv.server_address[1]
    for k, v in {"VAULT": vault, "PORT": port, "HOST": f"127.0.0.1:{port}", "ORIGIN": f"http://127.0.0.1:{port}",
                 "MAP": vault / ".wiki-inspi/map", "ATT": vault / "_attachments", "TRASH": vault / ".trash/wiki-inspi",
                 "_rebuild": lambda: None}.items():
        monkeypatch.setattr(SV, k, v)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield port
    srv.shutdown()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def call(port, path, body=None, host=None, origin="same"):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=None if body is None else json.dumps(body).encode(),
                                 method="GET" if body is None else "POST")
    req.add_header("Host", host or f"127.0.0.1:{port}")
    if origin:
        req.add_header("Origin", f"http://127.0.0.1:{port}" if origin == "same" else origin)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=10) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def test_saved_endpoint_guards_and_roundtrip(server):
    assert call(server, "/saved", {"name": "x"}, origin="https://evil.example")[0] == 403
    assert call(server, "/saved", host="evil.example")[0] == 403
    assert call(server, "/saved", {"name": "<script>"})[0] == 400 or json.loads(call(server, "/saved")[1])[0]["name"] == "script"
    st, out, _ = call(server, "/saved", {"name": "bleus", "query": "a", "color": "#112233"})
    assert st == 200 and any(s["name"] == "bleus" for s in json.loads(out))
    st, out, _ = call(server, "/saved", {"name": "bleus", "delete": True})
    assert st == 200 and not any(s["name"] == "bleus" for s in json.loads(out))


def test_today_is_stable_and_logged_once(server, vault):
    res = []
    ts = [threading.Thread(target=lambda: res.append(call(server, "/today.jpg"))) for _ in range(6)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert all(r[0] == 200 and r[2]["Content-Type"] == "image/jpeg" for r in res)
    assert len({r[1] for r in res}) == 1
    lines = (vault / ".wiki-inspi" / "today.jsonl").read_text().splitlines()
    assert len(lines) == 1
    st, _, h = call(server, "/today")
    assert st == 302 and h["Location"] == "/#voir=" + urllib.request.quote(json.loads(lines[0])["rel"], safe="")


def test_today_empty_vault_is_204(server, vault):
    (vault / ".wiki-inspi/map/data.js").write_text('window.INSPI={"items":[]};')
    assert call(server, "/today.jpg")[0] == 204


def test_one_broken_board_does_not_stop_the_others(vault, monkeypatch):
    S.put(vault, {"name": "a" * 80, "color": "#1e64d2", "tol": 10, "board": True})
    S.put(vault, {"name": "rouges", "color": "#c81e1e", "tol": 10, "board": True})
    real = L.write_board

    def boom(v, name, *a, **k):
        if name.startswith("auto-aaa"):
            raise ValueError("broken")
        return real(v, name, *a, **k)

    monkeypatch.setattr(L, "write_board", boom)
    items, by_rel = items_for(vault)
    rep = S.fill_boards(vault, items, {}, by_rel)
    assert any("error" in r for r in rep) and "auto-rouges: +1" in rep
    assert len(S.load(vault)[0]["name"]) <= 75
    ledger = json.loads((vault / ".wiki-inspi" / "saved-ledger.json").read_text())
    assert len(ledger["auto-rouges"]) == 1
