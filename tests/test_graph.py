import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from tessera import graph, ui
from tessera.extract import ActivityStore


def _act(project, how, public=True, story=2, area="software"):
    return {"project": project, "life_area": area, "what": f"did {project}", "how": how,
            "outcome": "", "lesson": "l", "public_ok": public, "story": story}  # fmt: skip


@pytest.fixture
def seeded(cfg):
    store = ActivityStore(cfg)
    rows = [
        ("claude_code:s1", [_act("GardenPlanner", ["plan md files", "benchmarks in git"])]),
        ("claude_code:s2", [_act("GardenPlanner sync", ["plan md files"])]),
        ("claude_code:s3", [_act("StarChart", ["random controls", "PLAN docs"], story=3)]),
        (
            "cowork:c1",
            [_act("Quarterly report", ["shared checklist"], public=False, area="work")],
        ),  # fmt: skip
    ]
    for uid, acts in rows:
        store.put({"unit_id": uid, "hash": "h", "source": uid.split(":")[0], "title": uid,
                   "folder": "f", "first": "2026-11-01T00:00:00+00:00",
                   "last": "2026-11-05T00:00:00+00:00", "event_ids": [uid],
                   "generalized": False, "tags": [], "activities": acts})  # fmt: skip
    store.save()
    return cfg


MERGED = {
    "projects": [
        {"name": "GardenPlanner", "area": "software", "kind": "private", "summary": "copy tool",
         "headline": "Building tools with AI",
         "raw": ["GardenPlanner", "GardenPlanner sync"]},
        {"name": "StarChart", "area": "science", "kind": "private", "summary": "pi",
         "headline": "Curiosity projects", "raw": ["StarChart"]},
    ],  # Quarterly report deliberately left out
    "headlines": [{"name": "Building tools with AI", "summary": "s"},
                  {"name": "Curiosity projects", "summary": "s"}],
    "methods": [
        {"name": "Plan files", "summary": "s", "items": [1, 0]},
        {"name": "Checklists", "summary": "s", "items": [4]},
    ],
}  # fmt: skip


class StreamClient:
    """Fake for client.beta.messages.stream(...) used by consolidate."""

    def __init__(self, data):
        self.data, self.calls = data, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))

    def _stream(self, **kw):
        self.calls.append(kw)
        msg = SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=json.dumps(self.data))],
        )
        cm = SimpleNamespace(get_final_message=lambda: msg)
        return SimpleNamespace(__enter__=lambda *a: cm, __exit__=lambda *a: False)


class CM:
    def __init__(self, obj):
        self.obj = obj

    def __enter__(self):
        return self.obj

    def __exit__(self, *a):
        return False


def _stream_client(data):
    c = StreamClient(data)
    c.beta.messages.stream = lambda **kw: (
        c.calls.append(kw),
        CM(
            SimpleNamespace(
                get_final_message=lambda: SimpleNamespace(
                    stop_reason="end_turn",
                    content=[SimpleNamespace(type="text", text=json.dumps(data))],
                )
            )
        ),
    )[1]
    return c


def test_consolidate_maps_names_and_methods_and_caches(seeded):
    # hows sorted: 0 PLAN docs, 1 benchmarks in git, 2 plan md files,
    # 3 random controls, 4 shared checklist
    data = json.loads(json.dumps(MERGED))
    data["methods"][0]["items"] = [0, 2]
    data["methods"][1]["items"] = [3]
    c = _stream_client(data)
    merged = graph.consolidate(seeded, client=c)
    assert any(p["name"] == "Quarterly report" and p["kind"] == "job" for p in merged["projects"])
    assert merged["method_of"]["plan md files"] == "Plan files"
    sent = c.calls[0]["messages"][0]["content"]
    assert "GardenPlanner sync" in sent and "2: plan md files" in sent

    graph.consolidate(seeded, client=_stream_client({}))  # cached: no new call needed
    g = graph.build_graph(seeded)
    labels = {n["label"]: n for n in g["nodes"]}
    assert labels["GardenPlanner"]["size"] == 2  # merged from two raw names
    assert labels["Plan files"]["size"] == 2  # GardenPlanner + StarChart
    assert {"from": "p:StarChart", "to": "m:Checklists", "weight": 1} in g["edges"]
    assert g["stats"]["projects"] == 3
    heads = {h["name"]: [p["name"] for p in h["children"]] for h in g["tree"]["children"]}
    assert heads == {
        "Building tools with AI": ["GardenPlanner"],
        "Curiosity projects": ["StarChart"],
        "Work": ["Quarterly report"],
    }
    leaf = g["tree"]["children"][0]["children"][0]["children"][0]
    assert leaf["type"] == "activity" and leaf["methods"] == ["Plan files"]

    # new activity -> merge runs again and gets the previous headlines to stay stable
    store = ActivityStore(seeded)
    row = dict(store.rows["claude_code:s1"], unit_id="claude_code:s9")
    row["activities"] = [_act("Bird feeder build", ["random controls"], area="making")]
    store.put(row)
    store.save()
    c2 = _stream_client(data)
    graph.consolidate(seeded, client=c2)
    sent2 = json.loads(c2.calls[0]["messages"][0]["content"])
    assert sent2["previous_headlines"] == ["Building tools with AI", "Curiosity projects"]


def _ready(cfg):
    data = json.loads(json.dumps(MERGED))
    data["methods"][0]["items"] = [0, 2]
    data["methods"][1]["items"] = [3]
    merged = graph.consolidate(cfg, client=_stream_client(data))
    return graph.build_graph(cfg), merged


class NoClient:
    beta = None


def test_share_view_hides_job_private_and_summaries(seeded):
    store = ActivityStore(seeded)
    row = dict(store.rows["claude_code:s3"], unit_id="claude_code:s4")
    row["activities"] = [_act("StarChart", ["random controls"], public=False, area="science")]
    store.put(row)
    store.save()
    g, _ = _ready(seeded)
    s = graph.share_view(g)
    heads = {h["name"]: [p["name"] for p in h["children"]] for h in s["tree"]["children"]}
    assert heads == {
        "Building tools with AI": ["GardenPlanner"],
        "Curiosity projects": ["StarChart"],
    }
    star = s["tree"]["children"][1]["children"][0]
    assert [a["public"] for a in star["children"]] == [True]  # the private one is gone
    assert all(h["summary"] == "" for h in s["tree"]["children"])
    assert all(m["summary"] == "" for m in s["methods"])
    text = json.dumps(s)
    assert "Quarterly report" not in text and "copy tool" not in text
    assert s["stats"]["projects"] == 2 and s["shared"] is True

    server = ThreadingHTTPServer(("127.0.0.1", 0), ui.make_handler(seeded, share=True))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/api/graph"
        assert "Quarterly report" not in urllib.request.urlopen(url).read().decode()
    finally:
        server.shutdown()


def test_map_stop_ends_the_running_map(seeded):
    import socket
    import subprocess
    import sys
    import time

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    code = ("import sys; from tessera.cli import main; "
            f"sys.exit(main(['map', '--no-browser', '--port', '{port}']))")  # fmt: skip
    env = {**__import__("os").environ, "TESSERA_HOME": str(seeded.root)}
    proc = subprocess.Popen([sys.executable, "-c", code], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)  # fmt: skip
    try:
        for _ in range(100):
            if (seeded.state_dir / "map.json").is_file():
                break
            time.sleep(0.1)
        assert "Stopped the Tessera map" in ui.stop(seeded)
        assert proc.wait(timeout=10) is not None  # the process is gone
        assert not (seeded.state_dir / "map.json").exists()
        assert ui.stop(seeded) == "No Tessera map is running."
    finally:
        proc.kill()


def test_map_port_in_use_is_a_clear_message(seeded, capsys):
    busy = ui.MapServer(("127.0.0.1", 0), ui.make_handler(seeded))
    try:
        with pytest.raises(SystemExit):
            ui.serve(seeded, port=busy.server_address[1], open_browser=False)
        assert "already in use" in capsys.readouterr().err
    finally:
        busy.server_close()


def test_map_survives_its_folder_being_deleted(seeded, tmp_path, monkeypatch):
    page = tmp_path / "index.html"
    page.write_text("<title>Tessera</title>", encoding="utf-8")
    monkeypatch.setattr(ui, "PAGE", page)
    server = ThreadingHTTPServer(("127.0.0.1", 0), ui.make_handler(seeded, share=True))
    page.unlink()  # a plugin update removed the old version's folder
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/"
        assert b"<title>Tessera</title>" in urllib.request.urlopen(url).read()
    finally:
        server.shutdown()


def test_map_serves_page_and_graph_only(seeded):
    _ready(seeded)
    server = ThreadingHTTPServer(("127.0.0.1", 0), ui.make_handler(seeded, client=NoClient()))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        page = urllib.request.urlopen(base + "/?theme=dark").read()
        assert b"<title>Tessera</title>" in page
        assert json.load(urllib.request.urlopen(base + "/api/graph"))["stats"]["projects"] == 3
        req = urllib.request.Request(base + "/api/anything", method="POST", data=b"{}")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 404  # the map only reads; nothing can be posted
    finally:
        server.shutdown()
