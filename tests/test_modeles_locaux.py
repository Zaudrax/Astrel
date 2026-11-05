"""Modèle local (plan P8, lot 2) : matériel détecté, ce qui tient, disque vérifié,
téléchargement par Ollama (faux serveur), Ollama absent = lien et commande seulement."""
import json
import stat
import sys
import time

import pytest

from astrel import config, modeles_locaux
from astrel.modeles_locaux import CATALOGUE, disk_need_go, place
from astrel.platform import materiel
from astrel.settings import reglages
from faux_ollama import FauxOllama

G = 1024 ** 3


def profil(name):
    return materiel._fake(name)


def proposes(name):
    props, hidden = modeles_locaux.local.proposals(profil(name))
    return {p["tag"]: p for p in props}, hidden


# ------------------------------------------------------------------ matériel

def test_profils_factices_et_json(monkeypatch):
    monkeypatch.setenv("ASTREL_MATERIEL_FAKE", "m2_16")
    mat = materiel.detect()
    assert mat.faux and mat.unifiee and mat.systeme == "macos" and mat.vram_go == pytest.approx(16 * 0.66, 0.01)
    monkeypatch.setenv("ASTREL_MATERIEL_FAKE", json.dumps({"gpu": "", "vram_go": 0, "ram_go": 4, "disque_go": 3}))
    mat = materiel.detect()  # autre profil : pas l'ancien gardé en cache
    assert mat.ram_go == 4 and mat.vram_go == 0 and mat.faux


def test_nvidia_smi_lu_et_carte_la_mieux_dotee(monkeypatch, tmp_path):
    if sys.platform == "win32":  # 03/11 : pas de « #! » sous Windows ; un .cmd se lance directement
        fake = tmp_path / "nvidia-smi.cmd"
        fake.write_text("@echo NVIDIA GeForce RTX 4060 Laptop GPU, 8188\r\n@echo Petite carte, 2048\r\n")
    else:
        fake = tmp_path / "nvidia-smi"
        fake.write_text("#!/bin/sh\necho 'NVIDIA GeForce RTX 4060 Laptop GPU, 8188'\necho 'Petite carte, 2048'\n")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(materiel, "_nvidia_smi", lambda: str(fake))
    assert materiel.nvidia() == ("NVIDIA GeForce RTX 4060 Laptop GPU", 8.0)
    monkeypatch.setattr(materiel, "_nvidia_smi", lambda: "")
    assert materiel.nvidia() == ("", 0.0)  # sans pilote : RAM seule


def test_dossier_des_modeles_est_celui_d_ollama(monkeypatch, tmp_path):
    """Règle de l'utilisateur (19/10) : la place se mesure sur le disque du VRAI dossier des modèles
    (ici le service ollama : OLLAMA_MODELS=/data/ollama/models), jamais sur / par défaut."""
    monkeypatch.delenv("ASTREL_OLLAMA_MODELS", raising=False)
    monkeypatch.delenv("OLLAMA_MODELS", raising=False)
    monkeypatch.setattr(materiel, "_service_models_dir", lambda: "/data/ollama/models")
    if materiel.sys.platform.startswith("linux"):
        assert str(materiel.models_dir()) == "/data/ollama/models"
    monkeypatch.setenv("OLLAMA_MODELS", str(tmp_path / "modeles"))
    assert materiel.models_dir() == tmp_path / "modeles"
    free, total = materiel.disk_go(tmp_path / "modeles" / "pas-encore-cree")
    assert 0 < free <= total  # dossier absent : le disque du premier parent existant


# ------------------------------------------------------------------ ce qui tient (étape 6)

def test_pc_de_utilisateur_9b_mixte_4b_rapide_70b_jamais():
    props, hidden = proposes("utilisateur")
    assert props["qwen3.5:9b"]["place"] == "mixte" and props["qwen3.5:4b"]["place"] == "gpu"
    assert "llama3.3:70b" not in props and "gpt-oss:120b" not in props and hidden >= 3
    assert all(place(p["size_go"], profil("utilisateur")) for p in props.values())


def test_sans_carte_8_go_petits_modeles_lents_seulement():
    props, hidden = proposes("cpu8")
    assert props and {p["place"] for p in props.values()} == {"cpu"}
    assert max(p["size_go"] for p in props.values()) + 1.5 <= 8 - 4
    assert "qwen3.5:9b" not in props and "qwen3.5:4b" not in props


def test_rtx_12_go_petits_et_gros():
    props, _ = proposes("rtx12")
    assert props["ministral-3:14b"]["place"] == "gpu"  # 8,5 Go + 1,5 ≤ 11,5
    assert props["qwen3.5:27b"]["place"] == "mixte" and "llama3.3:70b" not in props
    sizes = [p["size_go"] for p in props.values()]
    assert min(sizes) < 2 and max(sizes) > 15  # petits ET gros


def test_mac_m2_memoire_unifiee():
    props, _ = proposes("m2_16")
    assert props["ministral-3:14b"]["place"] == "gpu" and "gpt-oss:20b" not in props


def test_disque_regle_de_utilisateur():
    """7 Go libres sur 96 : tout est refusé (il faut garder 10 Go et 15 % du disque) ; sur
    /data (631 libres sur 741) : permis. Taille × 1,2 + 2 reste le minimum du plan."""
    props, _ = proposes("utilisateur")
    assert not any(p["disk_ok"] for p in props.values())
    props, _ = proposes("utilisateur_data")
    assert all(p["disk_ok"] for p in props.values())
    assert disk_need_go(6.1, 96) == pytest.approx(6.1 + 14.4)
    assert disk_need_go(60, 0) == pytest.approx(60 * 1.2 + 2)


def test_catalogue_valide():
    tags = [m.tag for m in CATALOGUE]
    assert len(tags) == len(set(tags)) and all(":" in t for t in tags)
    assert min(m.ref_octets for m in CATALOGUE) < 2 * G and max(m.ref_octets for m in CATALOGUE) > 40 * G


# ------------------------------------------------------------------ Ollama (faux serveur)

@pytest.fixture
def ollama(monkeypatch):
    with FauxOllama(manifests={"qwen3.5:4b": 3_320_000_000, "llama3.2:3b": 2_020_000_000}) as srv:
        monkeypatch.setattr(config, "OLLAMA_URL", srv.url)
        yield srv


def wait(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_telechargement_progression_puis_choisi(ollama):
    local = modeles_locaux.local
    seen = []
    local.on_change = lambda: seen.append(local.models_view())
    res = local.download("qwen3.5:4b")
    assert res["ok"], res
    assert wait(lambda: local._dl and local._dl["status"] == "fini")
    assert "qwen3.5:4b" in ollama.installed and local.chosen == "qwen3.5:4b"  # premier installé : choisi
    assert local.ready() == "qwen3.5:4b"
    view = local.models_view()
    lines = {l["label"]: l["text"] for l in view["value"]["lines"]}
    assert "installé" in lines["qwen3.5:4b"] and "choisi" in lines["qwen3.5:4b"]
    ids = {a["id"] for a in view["actions"]}
    assert "delete:qwen3.5:4b" in ids and "pull:qwen3.5:4b" not in ids and "pull:llama3.2:3b" in ids


def test_annulation(ollama):
    ollama.pull_steps, ollama.pull_delay = 50, 0.05
    local = modeles_locaux.local
    assert local.download("llama3.2:3b")["ok"]
    assert wait(lambda: local._dl["done"] > 0)
    assert "cancel" in {a["id"] for a in local.models_view()["actions"]}
    assert local.cancel()["ok"]
    assert wait(lambda: local._dl["status"] == "annulé")
    assert "llama3.2:3b" not in ollama.installed


def test_trop_gros_jamais_telecharge_et_disque_refuse(ollama, monkeypatch):
    local = modeles_locaux.local
    res = local.download("llama3.3:70b")
    assert not res["ok"] and "ne tient pas" in res["error"]
    monkeypatch.setenv("ASTREL_MATERIEL_FAKE", "utilisateur")
    res = local.download("qwen3.5:4b")
    assert not res["ok"] and "OLLAMA_MODELS" in res["error"] and "/usr/share/ollama" in res["error"]
    act = next(a for a in local.models_view()["actions"] if a["id"] == "pull:qwen3.5:4b")
    assert act["disabled"] and "OLLAMA_MODELS" in act["reason"]
    assert not [r for r in ollama.requests if r[1] == "/api/pull"]  # rien n'a été demandé à Ollama


def test_suppression_et_choix(ollama):
    ollama.installed.update({"qwen3.5:4b": 3_320_000_000, "astrel-qwen3.5-4b": 1})
    local = modeles_locaux.local
    assert local.choose("qwen3.5:4b")["ok"] and local.chosen == "qwen3.5:4b"
    assert not local.choose("llama3.2:3b")["ok"]  # pas installé
    local.set_active(True, "astrel-qwen3.5-4b")
    assert not local.remove("qwen3.5:4b")["ok"]  # mode léger en cours
    local.set_active(False)
    assert local.remove("qwen3.5:4b")["ok"]
    assert "qwen3.5:4b" not in ollama.installed and "astrel-qwen3.5-4b" not in ollama.installed
    assert local.chosen == ""


def test_modele_derive_et_repli(ollama):
    local = modeles_locaux.local
    assert local.prepare("qwen3.5:4b") == "astrel-qwen3.5-4b"
    create = [r for r in ollama.requests if r[1] == "/api/create"][-1][2]
    assert create["from"] == "qwen3.5:4b" and create["parameters"]["num_ctx"] == config.LOCAL_NUM_CTX
    ollama.create_error = "chtimes …: operation not permitted"  # vu sur la machine de l'utilisateur le 19/10
    assert local.prepare("qwen3.5:4b") == "qwen3.5:4b"


def test_taille_lue_au_manifeste(ollama, monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_REGISTRY", ollama.url)
    assert modeles_locaux.manifest_size("qwen3.5:4b") == 3_320_000_000
    assert modeles_locaux.manifest_size("qwen3.5:404b") == -1
    local = modeles_locaux.local
    local._refresh_sizes()
    assert wait(lambda: not local._sizes_busy and local._sizes)
    assert local.size_go("qwen3.5:4b") == (3_320_000_000 / G, True)
    props, _ = local.proposals()
    assert "qwen3.5:9b" not in {p["tag"] for p in props}  # absent du faux registre : retiré


def test_ollama_absent_lien_et_commande_rien_installe(monkeypatch):
    monkeypatch.setattr(modeles_locaux, "ollama_installed", lambda: False)
    view = modeles_locaux.local.materiel_view()["value"]
    assert view["link"]["url"] == "https://ollama.com/download" and "install.sh" in view["block"]
    assert any("pas installé" in l["text"] for l in view["lines"])
    assert not modeles_locaux.local.download("qwen3.5:4b")["ok"]
    monkeypatch.setenv("ASTREL_MATERIEL_FAKE", "cpu8")  # Windows : lien seulement
    assert "block" not in modeles_locaux.local.materiel_view()["value"]


def test_reglages_modele_local_par_la_page(ollama):
    d = {i["key"]: i for g in reglages.describe()["groups"] if g["id"] == "modele_local" for i in g["items"]}
    assert set(d) == {"moteur_mode", "local.mode", "local.materiel", "local.modeles", "local.commandes"}  # 04/11 ; 05/11 (lot 5)
    assert d["local.mode"]["locked"]  # sans le cœur : « Réglable quand Astrel tourne »
    res = reglages.action("local.modeles", "pull:qwen3.5:4b")
    assert res["ok"] and wait(lambda: "qwen3.5:4b" in ollama.installed)
    assert not reglages.action("local.modeles", "pull:llama3.3:70b")["ok"]  # bouton jamais affiché


def test_annulation_pendant_un_blocage_reseau_reste_annule(monkeypatch):
    """Relecture du 19/10 : annulé pendant une lecture bloquée, `pull` finit sur une exception
    (délai de lecture) ; c'est un « annulé », pas un « échec »."""
    local = modeles_locaux.local
    stop = modeles_locaux.threading.Event()

    def bloque(tag, on_progress, stop_ev):
        stop_ev.set()  # l'utilisateur clique « Annuler » pendant que la lecture attend
        raise TimeoutError("read timeout")

    monkeypatch.setattr(modeles_locaux, "pull", bloque)
    local._dl = {"tag": "llama3.2:3b", "status": "en cours", "done": 0, "total": 1, "step": "", "error": ""}
    local._pull("llama3.2:3b", stop)
    assert local._dl["status"] == "annulé" and local._dl["error"] == ""
    monkeypatch.setattr(modeles_locaux, "pull", lambda *a: (_ for _ in ()).throw(RuntimeError("disque plein")))
    local._dl = {"tag": "llama3.2:3b", "status": "en cours", "done": 0, "total": 1, "step": "", "error": ""}
    local._pull("llama3.2:3b", modeles_locaux.threading.Event())
    assert local._dl["status"] == "échec" and "disque plein" in local._dl["error"]  # sans annulation : échec


def test_une_seule_sonde_d_ollama_par_page(monkeypatch):
    """03/11 (CI Windows) : un port local fermé n'y répond qu'au bout du délai (1 s par sonde) ; la page des
    Réglages ne sonde Ollama qu'une fois par 5 s, « Relire » repart de zéro."""
    sondes = []
    monkeypatch.setattr(modeles_locaux, "ollama_version", lambda timeout=1.0: sondes.append(1) or "")
    local = modeles_locaux.local
    local.materiel_view(), local.models_view()
    reglages.describe()
    assert len(sondes) == 1
    assert local.run("refresh", {})["ok"]
    local.materiel_view()
    assert len(sondes) == 2
