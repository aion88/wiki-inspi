import json
import shutil
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import inspi_board_add as BA  # noqa: E402
import inspi_facets as F  # noqa: E402
import inspi_map as M  # noqa: E402
import inspilib as L  # noqa: E402
from test_inspi import make_note  # noqa: E402

# LZString.compressToBase64 of PLAIN, produced by lz-string 1.4.4 (the library Excalidraw uses).
PLAIN = (
    '{"type":"excalidraw","elements":[{"id":"a","type":"rectangle","x":10,"y":20,"width":30,'
    '"height":40}],"note":"é ü 漢字 repeated repeated repeated"}'
)
B64 = (
    "N4IgLgngDgpiBcIYA8DGBDANgSwCYCd0B3EAGiUxgFsYA7MAZwQG1Q8ER0zxo5F8YqMOloBzSt2QIAjAAZyEBACZ5IInjAALBAGZ"
    "VmmNlGawCACyyAvgF1ytAPZg+IAJcACAD9vARPaB1bTcCsOhOuAEwQSFhETC4IJZAA=="
)


@pytest.fixture
def vault(tmp_path):
    common = ["type: image", "schema_version: 2"]
    make_note(
        tmp_path,
        "ink-woman",
        common
        + [
            'technique: ["ink-wash", "watercolor"]',
            'style: ["figurative"]',
            'subject: "a woman standing in a field"',
            'main_color: "#1e66d0"',
            'mood: ["calm"]',
        ],
    )
    make_note(
        tmp_path,
        "ink-woman-2",
        common
        + [
            'technique: ["ink-wash"]',
            'style: ["figurative"]',
            'subject: "a woman by the sea"',
            'main_color: "#1e60c0"',
            'mood: ["calm"]',
        ],
        color=(30, 100, 210),
    )
    make_note(
        tmp_path,
        "dashboard",
        common
        + [
            'technique: ["vector", "flat-color"]',
            'style: ["dark-mode", "flat-ui"]',
            'subject: "an analytics interface with charts"',
            'main_color: "#111111"',
        ],
        color=(17, 17, 17),
    )
    # a byte-identical copy of the first picture under another, not yet classified, note
    (tmp_path / "Inbox").mkdir()
    shutil.copy(
        tmp_path / "_attachments/ink-woman.png", tmp_path / "_attachments/copy.png"
    )
    (tmp_path / "Inbox/copy.md").write_text(
        "---\nthumbnail: _attachments/copy.png\ntype: image\n---\n# Copy\n\n![[_attachments/copy.png]]\n"
    )
    return tmp_path


def test_facets_words_not_substrings():
    f = F.facets(
        {
            "technique": ["ink-wash", "watercolor"],
            "subject": "a woman standing in a field",
            "main_color": "#1e66d0",
            "color_temperature": "cool",
        }
    )
    assert "Encre" in f["te"] and "Aquarelle" in f["te"] and "Peinture" in f["ty"]
    assert {"Personne", "Paysage & nature"} <= set(f["su"])
    assert f["co"] == ["Bleu"] and f["tp"] == ["Froid"]
    g = F.facets(
        {
            "technique": ["screenshot"],
            "subject": "a user interface surface with a manual",
        }
    )
    assert "Personne" not in g["su"] and "Interface" in g["ty"]


@pytest.mark.parametrize(
    "hexc,name",
    [
        ("#111111", "Noir"),
        ("#f5f5f0", "Blanc & crème"),
        ("#d32f2f", "Rouge"),
        ("#1e66d0", "Bleu"),
        ("#388e3c", "Vert"),
        ("nope", None),
    ],
)
def test_color_name(hexc, name):
    assert F.color_name(hexc) == name


def test_lz_decompress_matches_lz_string():
    assert BA.lz_decompress_b64(B64) == PLAIN
    assert (
        BA.lz_decompress_b64(B64[:40] + "\n" + B64[40:]) == PLAIN
    )  # Excalidraw wraps lines


def test_map_build(vault):
    page = M.build(vault)
    data = json.loads((page.parent / "data.js").read_text()[len("window.INSPI=") : -1])
    by_title = {it["t"]: it for it in data["items"]}
    assert len(data["items"]) == 4 and by_title["Copy"]["f"] == -2 and all(
        (page.parent / it["img"]).is_file() for it in data["items"]
    )
    woman = by_title["Ink-Woman"]
    assert "Encre" in woman["fa"]["te"] and woman["r"] == "Library/ink-woman.md"
    near = [j for j, _ in woman["nb"]]
    assert (
        by_title["Ink-Woman-2"]["i"] in near
    )  # the closest neighbour is the other ink figure
    exact = [g for g in data["dups"] if g["exact"]]
    assert len(exact) == 1 and {by_title["Copy"]["i"], woman["i"]} == set(
        exact[0]["ids"]
    )
    assert exact[0]["ids"][0] == woman["i"]  # the classified note is the one kept


def test_board_append_keeps_existing(vault):
    notes = {n["path"].stem: n for n in L.load_notes(vault)}
    out, _, _ = L.write_board(vault, "b", [notes["ink-woman"]])
    before = BA.read_drawing(out.read_text())[0]["elements"]
    path, added, skipped = BA.append(
        vault, "b", [notes["dashboard"], notes["ink-woman"], notes["dashboard"]]
    )
    after = BA.read_drawing(path.read_text())[0]["elements"]
    assert (added, skipped) == (1, 2)
    assert after[: len(before)] == before  # nothing already on the board moved
    new = [e for e in after[len(before) :] if e["type"] == "image"]
    assert len(new) == 1 and new[0]["y"] > max(e["y"] + e["height"] for e in before)
    ids = [e["id"] for e in after]
    assert len(ids) == len(set(ids))
    assert any(p.name.startswith("b.") for p in BA.BACKUP.glob("b.*"))
    for p in BA.BACKUP.glob("b.*"):
        p.unlink()
    with pytest.raises(ValueError):
        BA.append(vault, "missing", [notes["dashboard"]])


@pytest.fixture
def server(vault, monkeypatch):
    import inspi_serve as S

    M.build(vault)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), S.H)
    port = srv.server_address[1]
    for k, v in {
        "VAULT": vault,
        "PORT": port,
        "HOST": f"127.0.0.1:{port}",
        "ORIGIN": f"http://127.0.0.1:{port}",
        "MAP": vault / ".wiki-inspi/map",
        "ATT": vault / "_attachments",
        "TRASH": vault / ".trash/wiki-inspi",
        "_rebuild": lambda: None,
    }.items():
        monkeypatch.setattr(S, k, v)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield port
    srv.shutdown()


def call(port, path, body=None, host=None, origin="same", ctype="application/json"):
    url = f"http://127.0.0.1:{port}{path}"
    req = urllib.request.Request(
        url,
        data=None if body is None else json.dumps(body).encode(),
        method="GET" if body is None else "POST",
    )
    req.add_header("Host", host or f"127.0.0.1:{port}")
    if origin:
        req.add_header(
            "Origin", f"http://127.0.0.1:{port}" if origin == "same" else origin
        )
    if body is not None:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_server_refuses_foreign_requests(server):
    assert call(server, "/")[0] == 200
    assert call(server, "/_attachments/dashboard.png")[0] == 200
    assert call(server, "/", host="evil.example")[0] == 403
    assert call(server, "/../Library/dashboard.md")[0] == 404
    body = {"name": "x", "notes": ["Library/dashboard.md"]}
    assert call(server, "/board", body, origin="https://evil.example")[0] == 403
    assert call(server, "/board", body, ctype="text/plain")[0] == 415


def test_server_board_delete_restore(server, vault):
    st, out = call(
        server, "/board", {"name": "!!!", "notes": ["Library/dashboard.md"]}
    )
    assert st == 400  # a name with no letter or digit is refused
    st, out = call(
        server, "/board", {"name": "my-board", "notes": ["Library/dashboard.md"]}
    )
    assert st == 200 and json.loads(out)["placed"] == 1
    assert (
        call(server, "/board", {"name": "my-board", "notes": ["Library/dashboard.md"]})[
            0
        ]
        == 409
    )
    st, out = call(
        server,
        "/board",
        {"name": "my-board", "mode": "add", "notes": ["Library/ink-woman.md"]},
    )
    assert st == 200 and json.loads(out)["placed"] == 1
    # delete: a moodboard path and a path outside the vault are ignored; an image a board uses stays
    st, out = call(
        server,
        "/delete",
        {
            "notes": [
                "Library/dashboard.md",
                "Moodboards/my-board.excalidraw.md",
                "../../etc/passwd",
                "Inbox/copy.md",
            ]
        },
    )
    res = json.loads(out)
    assert st == 200 and res["notes"] == 2
    assert (
        not (vault / "Library/dashboard.md").exists()
        and (vault / "_attachments/dashboard.png").exists()
    )
    assert not (
        vault / "_attachments/copy.png"
    ).exists()  # unused elsewhere: moved to the trash
    assert (vault / "Moodboards/my-board.excalidraw.md").exists()
    st, out = call(server, "/restore", {"batch": res["batch"]})
    assert (
        st == 200
        and (vault / "Library/dashboard.md").exists()
        and (vault / "_attachments/copy.png").exists()
    )
    assert call(server, "/restore", {"batch": "../x"})[0] == 400
