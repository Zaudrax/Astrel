"""Icône d'un connecteur nouveau récupérée d'elle-même (19/10, demande de l'utilisateur : « si on
ajoute un connecteur, celui-ci doit avoir son icône qui arrive en même temps »).

Aucun appel réseau réel : `icones._download` est remplacé par un faux (dictionnaire d'URL),
et le vrai `_download` n'est essayé qu'avec `httpx.MockTransport`."""
import hashlib
import json

import httpx
import pytest

from astrel import claudeai, icones

REAL_DOWNLOAD = icones._download  # pris à l'import, avant que conftest ne le remplace

ZORBLO_SVG = (b'<svg role="img" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><title>Zorblo</title>'
              b'<path d="M4.459 4.208c.746.606 1.026.56 2.428.466l13.215-.793z"/></svg>')
DATA = [
    {"title": "Zorblo", "slug": "zorblo", "hex": "000000", "source": "https://zorblo.example"},
    {"title": "Exemplo Cloud", "hex": "FFD21E", "aliases": {"aka": ["EC"]}},
    {"title": "Bylic", "slug": "bylic", "hex": "123456", "license": {"type": "CC-BY-4.0"}},
    {"title": "Evil", "slug": "../evil", "hex": "FFFFFF"},
]


class FakeNet:
    def __init__(self, data=DATA, svgs=None):
        self.raw = json.dumps(data).encode()
        self.svgs = {"zorblo": ZORBLO_SVG, "exemplocloud": ZORBLO_SVG.replace(b"Zorblo", b"Exemplo Cloud")}
        self.svgs.update(svgs or {})
        self.calls: list[str] = []
        self.down = False

    def __call__(self, url, max_bytes):
        self.calls.append(url)
        if self.down:
            return None
        if url == icones.DATA_URL:
            return self.raw
        prefix = icones.CDN_BASE + "icons/"
        if url.startswith(prefix) and url.endswith(".svg"):
            return self.svgs.get(url[len(prefix):-4])
        return None


@pytest.fixture
def net(monkeypatch):
    fake = FakeNet()
    monkeypatch.setattr(icones, "_download", fake)
    monkeypatch.setattr(icones, "DATA_SHA256", hashlib.sha256(fake.raw).hexdigest())
    return fake


def _misses():
    return json.loads(icones.STATE_FILE.read_text(encoding="utf-8"))["absent"]


def test_icone_recuperee_puis_servie(net):
    assert icones.for_name("claude.ai Zorblo")["src"] is None
    assert icones.fetch_missing(["claude.ai Zorblo"]) is True
    got = icones.for_name("claude.ai Zorblo")
    assert got["src"] == "assets/icons/auto/zorblo.svg" and got["color"] != "#000000"  # noir éclairci
    assert (icones._auto_dir() / "zorblo.svg").read_bytes() == ZORBLO_SVG
    index = json.loads((icones._auto_dir() / "index.json").read_text(encoding="utf-8"))
    assert index["icons"]["zorblo"]["hex"] == "000000" and "CC0" in index["source"]
    # Hôte fixe, version fixée, HTTPS seulement.
    assert net.calls == [icones.DATA_URL, icones.CDN_BASE + "icons/zorblo.svg"]
    assert all(u.startswith("https://cdn.jsdelivr.net/npm/simple-icons@16.35.0/") for u in net.calls)
    # Déjà là : plus aucun téléchargement, même pas les données.
    net.calls.clear()
    assert icones.fetch_missing(["claude.ai Zorblo", "claude.ai Gmail"]) is False and net.calls == []


def test_donnees_gardees_en_cache_et_titre_resolu(net, monkeypatch):
    assert icones.fetch_missing(["claude.ai Zorblo"])
    monkeypatch.setattr(icones, "_table_cache", None)  # comme après une relance d'Astrel
    net.calls.clear()
    assert icones.fetch_missing(["claude.ai Exemplo Cloud Hub"])  # titre, puis sans le dernier mot
    assert net.calls == [icones.CDN_BASE + "icons/exemplocloud.svg"]  # données relues du disque
    assert icones.for_name("claude.ai Exemplo Cloud Hub")["src"] == "assets/icons/auto/exemplocloud.svg"


def test_absente_negatif_date_sans_boucle(net, monkeypatch):
    assert icones.fetch_missing(["claude.ai Canva"]) is False
    assert _misses()["canva"]["why"] == "absente de simple-icons"
    assert icones.for_name("claude.ai Canva")["src"] is None and icones.for_name("claude.ai Canva")["letter"] == "C"
    monkeypatch.setattr(icones, "_table_cache", None)
    net.calls.clear()
    for _ in range(3):  # chaque découverte (toutes les 10 min) : rien
        assert icones.fetch_missing(["claude.ai Canva"]) is False
    assert net.calls == []
    later = icones.time.time() + icones.RETRY_ABSENT_S + 1
    monkeypatch.setattr(icones.time, "time", lambda: later)
    icones.fetch_missing(["claude.ai Canva"])
    # Nouvel essai après la date (données relues du cache disque, pas du réseau), nouvelle date.
    assert net.calls == [] and _misses()["canva"]["at"] == later
    monkeypatch.setattr(icones, "DATA_FILE", icones.DATA_FILE.with_name("absent.json"))
    monkeypatch.setattr(icones, "_table_cache", None)
    monkeypatch.setattr(icones.time, "time", lambda: later + icones.RETRY_ABSENT_S + 1)
    icones.fetch_missing(["claude.ai Canva"])
    assert net.calls == [icones.DATA_URL]  # sans cache disque : données retéléchargées, une fois


def test_reseau_coupe_reessai_le_lendemain_seulement(net, monkeypatch):
    net.down = True
    assert icones.fetch_missing(["claude.ai Zorblo"]) is False
    assert _misses()["zorblo"]["why"] == "réseau" and len(net.calls) == 1
    net.down = False
    assert icones.fetch_missing(["claude.ai Zorblo"]) is False and len(net.calls) == 1
    later = icones.time.time() + icones.RETRY_ERROR_S + 1
    monkeypatch.setattr(icones.time, "time", lambda: later)
    assert icones.fetch_missing(["claude.ai Zorblo"]) is True and "zorblo" not in _misses()


def test_empreinte_fausse_rien_ecrit(net, monkeypatch):
    monkeypatch.setattr(icones, "DATA_SHA256", "0" * 64)
    assert icones.fetch_missing(["claude.ai Zorblo"]) is False
    assert not icones.DATA_FILE.exists() and not icones._auto_dir().exists()
    assert _misses()["zorblo"]["why"] == "réseau"


def test_cache_disque_abime_retelecharge(net, monkeypatch):
    icones.DATA_FILE.parent.mkdir(parents=True)
    icones.DATA_FILE.write_text("[]", encoding="utf-8")  # empreinte fausse
    assert icones.fetch_missing(["claude.ai Zorblo"]) and net.calls[0] == icones.DATA_URL


def test_licence_autre_que_cc0_ecartee(net):
    assert icones.fetch_missing(["claude.ai Bylic"]) is False
    assert _misses()["bylic"]["why"] == "licence CC-BY-4.0"
    assert icones.CDN_BASE + "icons/bylic.svg" not in net.calls


def test_nom_jamais_dans_un_chemin(net, tmp_path):
    assert icones.fetch_missing(["claude.ai ../../evil", "claude.ai Evil"]) is False
    assert _misses()["evil"]["why"] == "données refusées"  # slug « ../evil » des données refusé
    assert not icones._auto_dir().exists() and not (tmp_path / "evil.svg").exists()
    assert all("evil" not in u for u in net.calls[1:])


def test_svg_dangereux_refuse(net):
    net.svgs["zorblo"] = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert icones.fetch_missing(["claude.ai Zorblo"]) is False
    assert _misses()["zorblo"]["why"] == "SVG refusé" and not (icones._auto_dir() / "zorblo.svg").exists()


SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{}</svg>'


@pytest.mark.parametrize("body", [
    SVG.format("<script>alert(1)</script>"),
    SVG.format('<foreignObject><div xmlns="http://www.w3.org/1999/xhtml">x</div></foreignObject>'),
    SVG.format('<path d="M0 0" onload="alert(1)"/>'),
    SVG.format('<path d="M0 0" onclick="x"/>'),
    SVG.format('<image href="https://exemple.org/x.png"/>'),
    SVG.format('<use xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="https://exemple.org/a.svg#x"/>'),
    SVG.format('<path d="M0 0" xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="#x"/>'),
    SVG.format('<path d="M0 0" style="fill:url(https://exemple.org/a)"/>'),
    SVG.format('<path d="M0 0" fill="url(https://exemple.org/a#g)"/>'),
    SVG.format('<path d="M0 0" fill="javascript:alert(1)"/>'),
    SVG.format("<style>path{fill:url(https://exemple.org)}</style>"),
    SVG.format("<!-- commentaire --><path d='M0 0'/>"),
    SVG.format("<![CDATA[x]]>"),
    '<?xml version="1.0"?><!DOCTYPE svg [<!ENTITY a "aaaa">]><svg xmlns="http://www.w3.org/2000/svg">&a;</svg>',
    '<?xml-stylesheet href="https://exemple.org/a.css"?>' + SVG.format(""),
    '<html><svg xmlns="http://www.w3.org/2000/svg"/></html>',
    '<svg viewBox="0 0 24 24"><path d="M0 0"/></svg>',  # sans espace de noms SVG
    SVG.format('<path d="M0 0"/>') + "<x",  # XML cassé
    SVG.format('<path d="' + "0" * 70_000 + '"/>'),  # trop gros
])
def test_svg_refuse(body):
    assert icones.safe_svg(body.encode()) is False


def test_svg_simple_accepte():
    assert icones.safe_svg(ZORBLO_SVG)
    assert icones.safe_svg(b'<?xml version="1.0" encoding="UTF-8"?>\n' + ZORBLO_SVG)
    assert not icones.safe_svg(b"\xff\xfe<svg/>") and not icones.safe_svg(b"")


# --------------------------------------------------------------------------- vrai _download, faux réseau

@pytest.fixture
def transport(monkeypatch):
    seen: list[httpx.Request] = []
    replies: dict[str, httpx.Response] = {}

    def handler(request):
        seen.append(request)
        return replies.get(str(request.url), httpx.Response(404))

    monkeypatch.setattr(icones, "_TRANSPORT", httpx.MockTransport(handler))
    return seen, replies


@pytest.mark.parametrize("url", [
    "http://cdn.jsdelivr.net/npm/simple-icons@16.35.0/icons/zorblo.svg",
    "https://exemple.org/icons/zorblo.svg",
    "https://cdn.jsdelivr.net.exemple.org/x.svg",
    "https://cdn.jsdelivr.net:8443/x.svg",
    "https://user:pw@cdn.jsdelivr.net/x.svg",
    "file:///etc/passwd",
])
def test_download_refuse_hors_liste(transport, url):
    seen, _ = transport
    assert REAL_DOWNLOAD(url, 1000) is None and seen == []  # refusé AVANT toute requête


def test_download_taille_redirection_statut(transport):
    seen, replies = transport
    base = icones.CDN_BASE + "icons/"
    replies[base + "ok.svg"] = httpx.Response(200, content=ZORBLO_SVG)
    replies[base + "gros.svg"] = httpx.Response(200, content=b"x" * 5000)
    replies[base + "annonce.svg"] = httpx.Response(200, content=b"x", headers={"content-length": "99999"})
    replies[base + "redir.svg"] = httpx.Response(302, headers={"location": "https://exemple.org/x.svg"})
    assert REAL_DOWNLOAD(base + "ok.svg", 1000) == ZORBLO_SVG
    assert REAL_DOWNLOAD(base + "gros.svg", 1000) is None
    assert REAL_DOWNLOAD(base + "annonce.svg", 1000) is None
    assert REAL_DOWNLOAD(base + "redir.svg", 1000) is None
    assert REAL_DOWNLOAD(base + "absent.svg", 1000) is None
    assert {r.url.host for r in seen} == {"cdn.jsdelivr.net"}  # la redirection n'est pas suivie


def test_download_erreur_reseau_silencieuse(monkeypatch):
    def boom(request):
        raise httpx.ConnectError("hors ligne", request=request)

    monkeypatch.setattr(icones, "_TRANSPORT", httpx.MockTransport(boom))
    assert REAL_DOWNLOAD(icones.DATA_URL, 1000) is None


# --------------------------------------------------------------------------- découverte → icône → page

def test_decouverte_ramene_l_icone_et_republie(net, monkeypatch, tmp_path):
    monkeypatch.setattr(claudeai, "discover", lambda: ["claude.ai Canva", "claude.ai Zorblo"])
    published = []
    c = claudeai.Connecteurs(on_change=lambda: published.append(icones.for_name("claude.ai Zorblo")["src"]))
    c.refresh(wait=True)
    # Une fois pour la liste (icône pas encore là), une fois quand l'icône arrive.
    assert published == [None, "assets/icons/auto/zorblo.svg"]
    assert icones.for_name("claude.ai Canva")["src"] is None  # pastille à lettre
    published.clear()
    c.refresh(wait=True)  # même liste, icônes connues : rien à republier, rien téléchargé
    assert published == [] and len(net.calls) == 2


def test_decouverte_reseau_coupe_page_intacte(net, monkeypatch):
    net.down = True
    monkeypatch.setattr(claudeai, "discover", lambda: ["claude.ai Zorblo"])
    published = []
    c = claudeai.Connecteurs(on_change=lambda: published.append(1))
    c.refresh(wait=True)
    assert published == [1] and c.names() == ["claude.ai Zorblo"]
