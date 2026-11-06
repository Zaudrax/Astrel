"""Barrière réseau du navigateur du modèle local (05/11/2026, lot 7, docs/chantiers/HYBRIDE-QWEN.md) : le
mandataire `proxy_local` laisse passer un site public (simulé : le résolveur rend une IP publique, la connexion est
redirigée vers un faux serveur local), refuse la boucle locale, le réseau privé, le rebinding, les IPv6 locales,
les ports autres que 80/443, le mode « rien ne sort », l'arrêt d'urgence ; plafonds, requêtes mal formées, mode
« adresses vues », journal sans chemin. Un essai RÉEL (Chrome headless par le Playwright MCP du local, vers
https://example.com et vers Ollama) est à part : `ASTREL_ESSAI_WEB=1`, jamais en CI."""
from __future__ import annotations

import asyncio
import http.server
import ipaddress
import json
import os
import socket
import subprocess
import threading
from pathlib import Path

import httpx
import pytest

from conftest import mode_prive

from astrel import config, hors_ligne, mcp_web_local, proxy_local, web_local
from astrel.settings import reglages

R = Path(__file__).resolve().parent.parent
PUBLIQUE = "93.184.215.14"
AUTRE_PUBLIQUE = "151.101.1.69"
NOMS = {"site-public.fr": [PUBLIQUE], "autre-public.fr": [AUTRE_PUBLIQUE], "prive.fr": ["192.168.1.10"],
        "melange.fr": [PUBLIQUE, "127.0.0.1"], "tailscale.fr": ["100.101.102.103"], "v6-local.fr": ["fd00::1"]}


class _Gestionnaire(http.server.BaseHTTPRequestHandler):
    """Faux serveur « public » : rend la ligne de requête et les en-têtes reçus."""
    protocol_version = "HTTP/1.1"
    taille = 0

    def _repondre(self):
        longueur = int(self.headers.get("Content-Length") or 0)
        corps_recu = self.rfile.read(longueur).decode() if longueur else ""
        corps = json.dumps({"ligne": self.requestline, "entetes": dict(self.headers.items()),
                            "corps": corps_recu}).encode()
        corps += b"x" * type(self).taille
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corps)))
        if self.headers.get("Connection", "").lower() == "close":
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(corps)

    do_GET = do_POST = _repondre  # noqa: N815

    def log_message(self, *a):
        pass


@pytest.fixture
def serveur():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Gestionnaire)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, args=(0.05,), daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()
    _Gestionnaire.taille = 0


@pytest.fixture
def lancer(serveur, tmp_path):
    """Fabrique de mandataires : résolveur de `NOMS`, connexions vers une IP publique redirigées vers le faux
    serveur (les IP réellement visées sont notées dans `m.visees`)."""
    lances = []

    def fabrique(**kw):
        visees: list[tuple[str, int]] = []
        resolutions: list[str] = []

        def resoudre(hote, port):
            resolutions.append(hote)
            if hote in NOMS:
                return list(NOMS[hote])
            try:
                return [str(ipaddress.ip_address(hote))]
            except ValueError:
                raise OSError("inconnu") from None

        def ouvrir(ip, port):
            visees.append((ip, port))
            return asyncio.open_connection("127.0.0.1", serveur)

        kw.setdefault("resolveur", resoudre)
        kw.setdefault("journal", tmp_path / "proxy.jsonl")
        m = proxy_local.Mandataire(ouvrir=ouvrir, **kw)
        m.start()
        m.visees, m.resolutions = visees, resolutions
        lances.append(m)
        return m

    yield fabrique
    for m in lances:
        m.stop()


@pytest.fixture
def hl():
    reglages.set(hors_ligne.CLE, True)
    yield
    reglages.reset()


def _brut(port: int, donnees: bytes, attendre: float = 5.0) -> bytes:
    """Envoie des octets bruts au mandataire, rend tout ce qui revient jusqu'à la fermeture."""
    with socket.create_connection(("127.0.0.1", port), timeout=attendre) as s:
        s.sendall(donnees)
        morceaux = []
        while True:
            try:
                b = s.recv(65536)
            except socket.timeout:
                break
            if not b:
                break
            morceaux.append(b)
    return b"".join(morceaux)


def _statut(reponse: bytes) -> int:
    return int(reponse.split(b" ", 2)[1]) if reponse.startswith(b"HTTP/1.") else 0


def _corps_json(reponse: bytes) -> dict:
    corps = reponse.split(b"\r\n\r\n", 1)[1]
    return json.JSONDecoder().raw_decode(corps.decode())[0]


# ------------------------------------------------------------------ ce qui passe

def test_http_en_clair_vers_un_site_public(lancer):
    m = lancer()
    r = _brut(m.port, b"GET http://site-public.fr/chemin/secret?jeton=42 HTTP/1.1\r\nHost: site-public.fr\r\n"
                      b"Proxy-Connection: keep-alive\r\nProxy-Authorization: Basic eA==\r\nUser-Agent: essai\r\n\r\n")
    assert _statut(r) == 200
    recu = _corps_json(r)
    assert recu["ligne"] == "GET /chemin/secret?jeton=42 HTTP/1.1"  # forme d'origine vers le site
    assert recu["entetes"]["Host"] == "site-public.fr"
    assert recu["entetes"]["Connection"] == "close"
    assert "Proxy-Connection" not in recu["entetes"] and "Proxy-Authorization" not in recu["entetes"]
    assert recu["entetes"]["User-Agent"] == "essai"
    assert m.visees == [(PUBLIQUE, 80)]  # connexion vers l'IP VÉRIFIÉE


def test_httpx_par_le_mandataire_et_corps_de_requete(lancer):
    m = lancer()
    with httpx.Client(proxy=m.adresse, trust_env=False, timeout=5) as c:
        r = c.get("http://site-public.fr/")
        assert r.status_code == 200 and r.json()["ligne"] == "GET / HTTP/1.1"
        r = c.post("http://site-public.fr:80/envoi", content=b"bonjour")
        assert r.json()["corps"] == "bonjour"
    assert m.bilan()["hotes"] == {"site-public.fr": 2}


def test_connect_tunnel_sans_dechiffrement(lancer):
    m = lancer()
    with socket.create_connection(("127.0.0.1", m.port), timeout=5) as s:
        s.sendall(b"CONNECT site-public.fr:443 HTTP/1.1\r\nHost: site-public.fr:443\r\n\r\n")
        assert s.recv(4096) == b"HTTP/1.1 200 Connection Established\r\n\r\n"
        # Le tunnel relaie les octets tels quels (ici du HTTP en clair, d'habitude du TLS).
        s.sendall(b"GET /dans-le-tunnel HTTP/1.1\r\nHost: site-public.fr\r\nConnection: close\r\n\r\n")
        r = b""
        while (b := s.recv(65536)):
            r += b
    assert _corps_json(r)["ligne"] == "GET /dans-le-tunnel HTTP/1.1"
    assert m.visees == [(PUBLIQUE, 443)]


def test_ipv6_publique_permise(lancer):
    m = lancer()
    r = _brut(m.port, b"CONNECT [2606:4700::6810:84e5]:443 HTTP/1.1\r\n\r\n", attendre=0.3)
    assert r.startswith(b"HTTP/1.1 200")
    assert m.visees == [("2606:4700::6810:84e5", 443)]


# ------------------------------------------------------------------ ce qui est refusé

@pytest.mark.parametrize("cible", [
    "127.0.0.1:443", "127.8.9.1:80", "localhost:443", "[::1]:443", "0.0.0.0:80",
    "10.0.0.1:443", "172.16.5.4:443", "192.168.1.1:80", "169.254.169.254:80", "100.100.100.100:443",
    "[fe80::1]:443", "[fc00::1]:443", "[fd12:3456::1]:443", "[::ffff:127.0.0.1]:443", "[::ffff:192.168.0.1]:443",
    "[64:ff9b::7f00:1]:443", "[2002:7f00:1::1]:443",
    "2130706433:443", "0x7f.1:443", "127.1:443", "017700000001:443",
    "box.lan:443", "nas.local:443", "routeur.home.arpa:443", "intranet:443", "machine.internal:443",
    "prive.fr:443", "melange.fr:443", "tailscale.fr:443", "v6-local.fr:443",
    "site-public.fr:8080", "site-public.fr:22", "site-public.fr:11434",
])
def test_connect_refuse(lancer, cible):
    m = lancer()
    r = _brut(m.port, f"CONNECT {cible} HTTP/1.1\r\nHost: {cible}\r\n\r\n".encode())
    assert _statut(r) == 403, r
    assert b"Astrel" in r
    assert m.visees == []  # aucune connexion tentée


@pytest.mark.parametrize("cible, statut", [
    ("http://127.0.0.1:11434/api/tags", 403),     # Ollama
    ("http://127.0.0.1/", 403),
    ("http://localhost:8899/search?q=x", 403),    # SearXNG
    ("http://[::1]/", 403),
    ("http://192.168.1.254/admin", 403),
    ("http://prive.fr/", 403),
    ("http://site-public.fr:8080/", 403),
    ("http://user:mdp@site-public.fr/", 400),
    ("https://site-public.fr/", 400),             # https en clair : jamais (CONNECT seulement)
    ("ftp://site-public.fr/", 400),
    ("/", 400),                                   # requête directe au mandataire
])
def test_http_refuse(lancer, cible, statut):
    m = lancer()
    r = _brut(m.port, f"GET {cible} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
    assert _statut(r) == statut, r
    assert m.visees == []


def test_refus_parlants_nom_avant_port(lancer):
    m = lancer()
    r = _brut(m.port, b"GET http://127.0.0.1:11434/ HTTP/1.1\r\n\r\n")
    assert "locale ou privée".encode() in r
    r = _brut(m.port, b"CONNECT site-public.fr:8443 HTTP/1.1\r\n\r\n")
    assert b"port 8443" in r


def test_rebinding_impossible(lancer):
    """Le nom est résolu UNE fois, par le mandataire ; la connexion part vers cette IP-là. Un nom qui répond
    autre chose la fois suivante est rejugé (refusé), et Chrome ne résout jamais lui-même."""
    reponses = {"bascule.fr": [[PUBLIQUE], ["127.0.0.1"]]}

    def resoudre(hote, port):
        return reponses[hote].pop(0)

    m = lancer(resolveur=resoudre)
    assert _brut(m.port, b"GET http://bascule.fr/ HTTP/1.1\r\n\r\n").startswith(b"HTTP/1.1 200")
    assert _statut(_brut(m.port, b"GET http://bascule.fr/ HTTP/1.1\r\n\r\n")) == 403
    assert m.visees == [(PUBLIQUE, 80)]


def test_hors_ligne_refuse(lancer, hl):
    m = lancer()
    r = _brut(m.port, b"CONNECT site-public.fr:443 HTTP/1.1\r\n\r\n")
    assert _statut(r) == 403 and "rien ne sort".encode() in r
    assert m.visees == [] and m.resolutions == []


def test_arret_d_urgence_refuse(lancer):
    m = lancer()
    config.ESTOP_FILE.write_text("1")
    try:
        r = _brut(m.port, b"CONNECT site-public.fr:443 HTTP/1.1\r\n\r\n")
    finally:
        config.ESTOP_FILE.unlink()
    assert _statut(r) == 503 and m.visees == []


def test_tunnel_ouvert_coupe_par_l_arret_d_urgence(lancer, monkeypatch):
    monkeypatch.setattr(proxy_local, "VEILLE_S", 0.1)
    m = lancer()
    with socket.create_connection(("127.0.0.1", m.port), timeout=5) as s:
        s.sendall(b"CONNECT site-public.fr:443 HTTP/1.1\r\n\r\n")
        assert s.recv(4096).startswith(b"HTTP/1.1 200")
        config.ESTOP_FILE.write_text("1")
        try:
            assert s.recv(10) == b""  # fermé par la veille
        finally:
            config.ESTOP_FILE.unlink()
    assert _statut(_brut(m.port, b"CONNECT site-public.fr:443 HTTP/1.1\r\n\r\n")) == 503  # tout le tour


def test_couper_ferme_et_refuse(lancer):
    m = lancer()
    with socket.create_connection(("127.0.0.1", m.port), timeout=5) as s:
        s.sendall(b"CONNECT site-public.fr:443 HTTP/1.1\r\n\r\n")
        assert s.recv(4096).startswith(b"HTTP/1.1 200")
        m.couper("Stop tout")
        assert s.recv(10) == b""
    r = _brut(m.port, b"CONNECT site-public.fr:443 HTTP/1.1\r\n\r\n")
    assert _statut(r) == 503 and b"Stop tout" in r


# ------------------------------------------------------------------ requêtes mal formées

@pytest.mark.parametrize("brut, statut", [
    (b"CONNECT site-public.fr HTTP/1.1\r\n\r\n", 400),                     # port manquant
    (b"CONNECT site-public.fr:443\r\n\r\n", 400),                          # pas de version
    (b"CONNECT  site-public.fr:443 HTTP/1.1\r\n\r\n", 400),                # double espace
    (b"CONNECT site-public.fr:443/x HTTP/1.1\r\n\r\n", 400),
    (b"CONNECT a@site-public.fr:443 HTTP/1.1\r\n\r\n", 400),
    (b"CONNECT [::1%25lo]:443 HTTP/1.1\r\n\r\n", 400),
    ("CONNECT exemple-é.fr:443 HTTP/1.1\r\n\r\n".encode(), 400),          # hors ASCII
    (b"CONNECT site-public.fr:443 HTTP/2.0\r\n\r\n", 400),
    (b"\x16\x03\x01\x02\x00\x01\x00\x01\xfc\x03\x03\r\n\r\n", 400),        # TLS envoyé au mandataire
    (b"TRACE http://site-public.fr/ HTTP/1.1\r\n\r\n", 405),
    (b"GET http://site-public.fr/ HTTP/1.1\r\nsans-deux-points\r\n\r\n", 400),
    (b"GET http://site-public.fr/ HTTP/1.1\r\n repli: x\r\n\r\n", 400),
    (b"POST http://site-public.fr/ HTTP/1.1\r\nContent-Length: 3\r\nTransfer-Encoding: chunked\r\n\r\n", 400),
])
def test_requetes_mal_formees(lancer, brut, statut):
    m = lancer()
    assert _statut(_brut(m.port, brut)) == statut
    assert m.visees == []


def test_en_tete_trop_long_ou_trop_lent(lancer, monkeypatch):
    monkeypatch.setattr(proxy_local, "DELAI_TETE_S", 0.3)
    m = lancer()
    long = b"GET http://site-public.fr/ HTTP/1.1\r\nX: " + b"a" * (proxy_local.TETE_MAX + 10) + b"\r\n\r\n"
    assert _statut(_brut(m.port, long)) == 400
    with socket.create_connection(("127.0.0.1", m.port), timeout=5) as s:
        s.sendall(b"CONNECT site-public.fr:443 HTTP/1.1\r\n")  # jamais fini
        assert _statut(s.recv(4096)) == 400
    assert m.visees == []


# ------------------------------------------------------------------ bornes du tour

def test_plafond_de_connexions(lancer):
    m = lancer(connexions_max=3)
    for _ in range(3):
        assert _brut(m.port, b"GET http://site-public.fr/ HTTP/1.1\r\n\r\n").startswith(b"HTTP/1.1 200")
    r = _brut(m.port, b"GET http://site-public.fr/ HTTP/1.1\r\n\r\n")
    assert _statut(r) == 429 and m.bilan()["plafond"] == "connexions"
    assert len(m.visees) == 3


def test_plafond_d_octets(lancer):
    _Gestionnaire.taille = 50_000
    m = lancer(octets_max=20_000)
    r = _brut(m.port, b"GET http://site-public.fr/gros HTTP/1.1\r\n\r\n")
    assert len(r) < 50_000  # coupé en route
    assert m.bilan()["plafond"] == "octets"
    assert _statut(_brut(m.port, b"GET http://site-public.fr/ HTTP/1.1\r\n\r\n")) == 429


def test_plafond_de_connexions_simultanees(lancer):
    m = lancer(simultanees_max=1)
    with socket.create_connection(("127.0.0.1", m.port), timeout=5) as s:
        s.sendall(b"CONNECT site-public.fr:443 HTTP/1.1\r\n\r\n")
        assert s.recv(4096).startswith(b"HTTP/1.1 200")
        assert _statut(_brut(m.port, b"CONNECT site-public.fr:443 HTTP/1.1\r\n\r\n")) == 429


# ------------------------------------------------------------------ mode « adresses vues »

def test_mode_tout_hote_public_par_defaut(lancer):
    m = lancer()
    assert m.mode == "public"
    assert _brut(m.port, b"CONNECT autre-public.fr:443 HTTP/1.1\r\n\r\n", attendre=0.3).startswith(b"HTTP/1.1 200")


def test_mode_adresses_vues(lancer):
    reg = web_local.AdressesVues()
    reg.ajouter("https://site-public.fr/article?id=3", "recherche")
    m = lancer(hotes_permis=proxy_local.hotes_de(reg))
    assert m.mode == "vues"
    assert _brut(m.port, b"CONNECT site-public.fr:443 HTTP/1.1\r\n\r\n", attendre=0.3).startswith(b"HTTP/1.1 200")
    r = _brut(m.port, b"CONNECT autre-public.fr:443 HTTP/1.1\r\n\r\n")
    assert _statut(r) == 403 and "jamais vu".encode() in r
    assert m.permettre("https://autre-public.fr/page?x=1", "file:///etc/passwd", "") == 1
    assert _brut(m.port, b"CONNECT autre-public.fr:443 HTTP/1.1\r\n\r\n", attendre=0.3).startswith(b"HTTP/1.1 200")
    # Un hôte permis mais privé reste refusé.
    m.permettre("prive.fr")
    assert _statut(_brut(m.port, b"CONNECT prive.fr:443 HTTP/1.1\r\n\r\n")) == 403


def test_hote_de():
    assert proxy_local.hote_de("https://WWW.Exemple.FR/a?b") == "www.exemple.fr"
    assert proxy_local.hote_de("exemple.fr") == "exemple.fr"
    assert proxy_local.hote_de("https://[2606:4700::1]/") == "2606:4700::1"
    assert proxy_local.hote_de("javascript:alert(1)") is None
    assert proxy_local.hotes_de(["https://a.fr/x", "https://a.fr/y", "http://b.fr"]) == {"a.fr", "b.fr"}


# ------------------------------------------------------------------ journal, cycle de vie

def test_journal_hotes_sans_chemin_ni_parametres(lancer, tmp_path):
    m = lancer()
    _brut(m.port, b"GET http://site-public.fr/chemin-prive?jeton=SECRET123 HTTP/1.1\r\n\r\n")
    _brut(m.port, b"GET http://192.168.1.1/admin?mdp=SECRET456 HTTP/1.1\r\n\r\n")
    m.stop()
    journal = tmp_path / "proxy.jsonl"
    texte = journal.read_text(encoding="utf-8")
    assert "SECRET" not in texte and "chemin-prive" not in texte and "admin" not in texte
    lignes = [json.loads(l) for l in texte.splitlines()]
    assert [(l["hote"], l["port"], l["verdict"]) for l in lignes] == [("site-public.fr", 80, "ok"),
                                                                     ("192.168.1.1", 80, "refus")]
    assert lignes[0]["octets"] > 0
    assert mode_prive(journal)  # 05/11 : pas de bits de mode sous Windows
    assert m.bilan()["connexions"] == 2 and m.bilan()["refus"] == 1


def test_ecoute_sur_la_boucle_locale_seulement_et_arret(tmp_path):
    with proxy_local.demarrer(journal=None) as m:
        assert m.adresse.startswith("http://127.0.0.1:") and m.port > 1024
        port = m.port
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
    m.stop()  # deux fois : sans effet


def test_pas_de_journal_reel_pendant_les_tests(lancer):
    m = lancer()
    assert m.journal != config.LOGS / proxy_local.JOURNAL_NOM


# ------------------------------------------------------------------ déclaration du navigateur

def test_declaration_playwright_par_le_mandataire(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "RUNTIME", tmp_path)
    assert mcp_web_local.declaration_playwright(None) is None  # jamais de navigateur sans barrière
    assert mcp_web_local.declaration_playwright("x") is None
    assert mcp_web_local.declaration_playwright(70000) is None
    pw = mcp_web_local.declaration_playwright(40123, tmp_path / "sortie")
    if pw is None:
        pytest.skip("Node ou mcp-servers/ absent")
    a = pw["args"]
    assert a[a.index("--proxy-server") + 1] == "http://127.0.0.1:40123"
    assert a[a.index("--proxy-bypass") + 1] == "<-loopback>"  # n'exempte RIEN, boucle locale comprise
    reglages_pw = Path(a[a.index("--config") + 1])
    assert reglages_pw.parent == tmp_path
    args_chrome = json.loads(reglages_pw.read_text())["browser"]["launchOptions"]["args"]
    assert "--webrtc-ip-handling-policy=disable_non_proxied_udp" in args_chrome
    env = pw["env"]
    assert env["PLAYWRIGHT_MCP_PROXY_SERVER"] == "http://127.0.0.1:40123"
    assert env["PLAYWRIGHT_MCP_PROXY_BYPASS"] == "<-loopback>"
    assert env["PLAYWRIGHT_DISABLE_FORCED_CHROMIUM_PROXIED_LOOPBACK"] == ""
    reglages_pw.write_text("{}")  # modifié entre deux tours : réécrit à la déclaration suivante
    mcp_web_local.declaration_playwright(40123)
    assert "webrtc" in reglages_pw.read_text()


def test_options_du_mandataire_dans_la_version_fixee():
    """@playwright/mcp 0.0.82 a --proxy-server et --proxy-bypass ; son playwright-core recopie le contournement
    dans --proxy-bypass-list (lanceur et contexte) et reconnaît « <-loopback »."""
    base = R / "mcp-servers" / "node_modules"
    readme = base / "@playwright" / "mcp" / "README.md"
    coeur = base / "playwright-core" / "lib" / "coreBundle.js"
    if not (readme.exists() and coeur.exists()):
        pytest.skip("mcp-servers/ non installé")
    assert "--proxy-server <proxy>" in readme.read_text(encoding="utf-8")
    assert "--proxy-bypass <bypass>" in readme.read_text(encoding="utf-8")
    texte = coeur.read_text(encoding="utf-8")
    assert '"<-loopback>"].some((host) => hosts.includes(host))' in texte
    assert "chromeArguments.push(`--proxy-bypass-list=${proxyBypassRules.join(\";\")}`)" in texte
    assert "launchOptions.proxy = proxy;" in texte and "contextOptions.proxy = proxy;" in texte


# ------------------------------------------------------------------ essai RÉEL : Chrome headless par le mandataire

class _MCP:
    """Client JSON-RPC minimal du Playwright MCP (stdio, une ligne par message)."""

    def __init__(self, decl: dict, cwd: Path):
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG")}
        env.update({k: v for k, v in decl["env"].items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")})
        self.p = subprocess.Popen([decl["command"], *decl["args"]], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, env=env, cwd=cwd, text=True)
        self.n = 0
        self.appel("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                  "clientInfo": {"name": "test-proxy-local", "version": "0"}})
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self.p.stdin.flush()

    def appel(self, methode: str, params: dict) -> dict:
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": methode, "params": params}) + "\n")
        self.p.stdin.flush()
        while True:
            ligne = self.p.stdout.readline()
            assert ligne, "Playwright MCP s'est arrêté"
            r = json.loads(ligne)
            if r.get("id") == self.n:
                return r

    def outil(self, nom: str, **args) -> str:
        r = self.appel("tools/call", {"name": nom, "arguments": args})
        return "\n".join(c.get("text", "") for c in r.get("result", {}).get("content", []))

    def fermer(self):
        try:
            self.outil("browser_close")
        finally:
            self.p.stdin.close()
            try:
                self.p.wait(15)
            except subprocess.TimeoutExpired:
                self.p.kill()


@pytest.mark.skipif(os.environ.get("ASTREL_ESSAI_WEB") != "1", reason="essai réel : ASTREL_ESSAI_WEB=1")
def test_essai_reel_chrome_par_le_mandataire(tmp_path, monkeypatch):
    """Le VRAI Chrome headless du Playwright MCP local, lancé par sa déclaration : https://example.com passe ;
    Ollama (127.0.0.1:11434, localhost), le port 80 de la machine et le réseau privé sont refusés PAR LE
    MANDATAIRE (`--blocked-origins` retiré pour cet essai, sinon Playwright les bloquerait avant) ; WebRTC ne
    donne aucun candidat UDP. Un seul Chrome headless à la fois sur la machine."""
    monkeypatch.setattr(config, "RUNTIME", tmp_path)
    if subprocess.run(["pgrep", "-f", "[h]eadless=new"], capture_output=True).returncode == 0:
        pytest.fail("un Chrome headless tourne déjà : un seul à la fois")
    with proxy_local.demarrer(journal=tmp_path / "proxy.jsonl") as m:
        decl = mcp_web_local.declaration_playwright(m.port, tmp_path / "sortie")
        assert decl, "Node ou mcp-servers/ absent"
        i = decl["args"].index("--blocked-origins")
        del decl["args"][i:i + 2]
        pw = _MCP(decl, tmp_path)
        try:
            pw.outil("browser_navigate", url="https://example.com/")
            texte = pw.outil("browser_evaluate", function="() => document.title + ' | ' + document.body.innerText")
            assert "Example Domain" in texte, texte
            for url in ("http://127.0.0.1:11434/", "http://localhost:11434/api/tags", "http://127.0.0.1/",
                        "http://192.168.1.1/", "http://[::1]:11434/"):
                pw.outil("browser_navigate", url=url)
                texte = pw.outil("browser_evaluate", function="() => document.body.innerText")
                assert "Ollama is running" not in texte and '"models"' not in texte, (url, texte)
                assert "Astrel" in texte, (url, texte)
            pw.outil("browser_navigate", url="https://example.com/")
            webrtc = pw.outil("browser_evaluate", function=(
                "async () => { const pc = new RTCPeerConnection({iceServers: [{urls: 'stun:stun.l.google.com:19302'}]});"
                " pc.createDataChannel('x'); const c = []; pc.onicecandidate = e => { if (e.candidate) "
                "c.push(e.candidate.candidate); }; await pc.setLocalDescription(await pc.createOffer());"
                " await new Promise(r => setTimeout(r, 4000)); pc.close(); return 'candidats=' + c.length + ' '"
                " + c.join(';'); }"))
            assert "candidats=0" in webrtc, webrtc
        finally:
            pw.fermer()
        bilan = m.bilan()
    journal = [json.loads(l) for l in (tmp_path / "proxy.jsonl").read_text(encoding="utf-8").splitlines()]
    assert bilan["hotes"].get("example.com"), bilan
    refuses = {(l["hote"], l["port"]) for l in journal if l["verdict"] == "refus"}
    for attendu in (("127.0.0.1", 11434), ("localhost", 11434), ("127.0.0.1", 80), ("192.168.1.1", 80),
                    ("::1", 11434)):
        assert attendu in refuses, (attendu, refuses)
    assert not any(l["hote"] in ("127.0.0.1", "localhost", "::1", "192.168.1.1") and l["verdict"] == "ok"
                   for l in journal)
