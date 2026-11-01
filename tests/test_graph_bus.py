from astrel import graph as graph_mod
from astrel.events import Bus


def test_bus_instantane_et_historique():
    b = Bus()
    seen = []
    b.subscribe(seen.append)
    b.publish("state", state="sleeping")
    b.publish("state", state="listening")
    b.publish("tool", tool="Bash")
    b.publish("level", value=0.3)
    snap = b.snapshot()
    assert [e["state"] for e in snap["sticky"]] == ["listening"]  # seule la dernière valeur
    assert [e["type"] for e in snap["history"]] == ["tool"]        # le niveau n'est pas gardé
    assert len(seen) == 4


def test_abonne_en_panne_ne_casse_rien():
    b = Bus()
    b.subscribe(lambda ev: 1 / 0)
    ok = []
    b.subscribe(ok.append)
    b.publish("notice", text="x")
    assert ok


def test_graphe_avec_coffre(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    (vault / "projets").mkdir(parents=True)
    (vault / "moi.md").write_text("# Moi\nAlternant. Voir [[Boutique]].", encoding="utf-8")
    (vault / "projets" / "boutique.md").write_text("# Boutique\nApp nutrition.", encoding="utf-8")
    monkeypatch.setattr(graph_mod, "VAULT", vault)
    monkeypatch.setattr(graph_mod, "USAGE_FILE", tmp_path / "usage.json")

    g = graph_mod.Graph()
    g.set_init({"model": "claude-opus-5-5", "tools": ["Bash", "Read", "mcp__astrel__set_volume"],
                "skills": ["deep-research", "caveman:caveman"], "agents": ["Explore"],
                "mcp_servers": [{"name": "astrel", "status": "connected"}]})
    data = g.build()
    ids = {n["id"] for n in data["nodes"]}
    assert {"core", "brain", "tool:Bash", "skill:deep-research", "plugin:caveman",
            "mcp:astrel", "mcp:astrel:set_volume", "sensor:EYES",
            "note:moi.md", "note:projets/boutique.md", "folder:projets"} <= ids
    note_links = {(l["source"], l["target"]) for l in data["links"] if l["kind"] == "lien"}
    assert ("note:moi.md", "note:projets/boutique.md") in note_links

    node = g.record_use("mcp__astrel__set_volume")
    assert node == "mcp:astrel:set_volume"
    uses = {n["id"]: n["uses"] for n in g.build()["nodes"]}
    assert uses["mcp:astrel:set_volume"] == 1


def test_graphe_sans_init_montre_les_outils_natifs(tmp_path, monkeypatch):
    monkeypatch.setattr(graph_mod, "VAULT", tmp_path / "absent")
    monkeypatch.setattr(graph_mod, "USAGE_FILE", tmp_path / "usage.json")
    data = graph_mod.Graph().build()
    assert any(n["id"] == "tool:Bash" for n in data["nodes"])
