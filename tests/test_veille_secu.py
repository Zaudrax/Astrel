"""Veille de sécurité (`astrel/veille_secu.py`) : réseau simulé, aucun appel réel."""
import json
import re
import sys
from datetime import datetime, timedelta

import pytest

from astrel import config
from astrel import veille_secu as v
from astrel.senses import checks
from conftest import programme_python

NOW = datetime(2026, 10, 19, 13, 0)        # un lundi
REAL_GET = v._get                          # avant que la fixture ne coupe le réseau
REAL_CHROME = v.find_chrome()              # avant que la fixture ne cache Chrome


@pytest.fixture(autouse=True)
def _isole(tmp_path, monkeypatch):
    out = tmp_path / "veille"
    monkeypatch.setattr(v, "OUT_DIR", out)
    monkeypatch.setattr(v, "REPORT_DIR", out)
    monkeypatch.setattr(v, "STATE_FILE", out / "etat.json")
    monkeypatch.setattr(v, "CACHE_FILE", out / "osv-cache.json")
    monkeypatch.setattr(v, "ROOTS", {"Dev": tmp_path / "Dev", "Divers": tmp_path / "Divers"})

    def interdit(*_a, **_k):
        raise AssertionError("réseau réel interdit dans les tests")
    monkeypatch.setattr(v, "_get", interdit)
    monkeypatch.setattr(v, "find_chrome", lambda: None)     # pas de vrai Chrome sauf test dédié
    monkeypatch.setattr(v.tempfile, "tempdir", str(tmp_path))  # HTML temporaire : vérifiable


# ---------------------------------------------------------------- dû / pas dû

@pytest.mark.parametrize("last, now, due", [
    (None, NOW, True),                                                   # jamais fait
    ("2026-10-19T08:05:00", datetime(2026, 10, 25, 22, 0), False),       # fait lundi, dimanche suivant
    ("2026-10-19T08:05:00", datetime(2026, 10, 26, 7, 59), False),       # lundi suivant avant 8 h
    ("2026-10-19T08:05:00", datetime(2026, 10, 26, 8, 0), True),         # lundi suivant 8 h
    ("2026-10-12T09:00:00", datetime(2026, 10, 21, 10, 0), True),        # PC éteint lundi : rattrapé mercredi
    ("2026-10-19T07:30:00", datetime(2026, 10, 19, 9, 0), True),         # fait avant 8 h ce lundi : pas compté
    ("illisible", NOW, True),
])
def test_rapport_du_selon_la_date(last, now, due):
    state = {"dernier_rapport": last} if last else {}
    assert v.report_due(now, state) is due


def test_dernier_lundi_8h():
    assert v.last_monday_8(datetime(2026, 10, 19, 7, 0)) == datetime(2026, 10, 12, 8, 0)
    assert v.last_monday_8(datetime(2026, 10, 19, 8, 0)) == datetime(2026, 10, 19, 8, 0)
    assert v.last_monday_8(datetime(2026, 10, 24, 1, 0)) == datetime(2026, 10, 19, 8, 0)


def test_alerte_due_toutes_les_6_heures():
    assert v.alert_due(NOW, {})
    assert not v.alert_due(NOW, {"dernier_controle_alerte": (NOW - timedelta(hours=5, minutes=59)).isoformat()})
    assert v.alert_due(NOW, {"dernier_controle_alerte": (NOW - timedelta(hours=6)).isoformat()})


def test_sous_commande_due_imprime_du_ou_pas_du(capsys):
    assert v.main(["due"]) == 0
    assert capsys.readouterr().out == "DU\n"
    v.save_state({"dernier_rapport": datetime.now().isoformat(timespec="seconds"),
                  "dernier_controle_alerte": datetime.now().isoformat(timespec="seconds")})
    v.main(["due"])
    v.main(["alert-due"])
    assert capsys.readouterr().out == "PAS-DU\nPAS-DU\n"


def test_compatible_avec_un_reflexe_check_commande():
    """Le moteur des réflexes accepte la commande (lecture seule pour le garde-fou) et
    `contains: ^DU` ne confond pas PAS-DU avec DU."""
    spec = {"kind": "command", "argv": [sys.executable, "-c", "print('PAS-DU')"], "contains": "^DU"}
    assert checks.run(spec)[0] is False
    assert checks.run({**spec, "argv": [sys.executable, "-c", "print('DU')"]})[0] is True
    checks.validate({"kind": "command", "argv": [".venv/bin/python", "-m", "astrel.veille_secu", "due"],
                     "cwd": str(config.ROOT), "contains": "^DU"})


# ---------------------------------------------------------------- sources publiques

RSS = b"""<?xml version='1.0' encoding='UTF-8'?><rss version="2.0"><channel>
<item><title>Vieille alerte</title><link>https://cert/1</link><description>x</description>
<pubDate>Fri, 24 Jul 2015 00:00:00 +0000</pubDate></item>
<item><title>Vuln\xc3\xa9rabilit\xc3\xa9 dans LibreNMS (15 octobre 2026)</title><link>https://cert/2</link>
<description>\\[Mise \xc3\xa0 jour\\] &lt;b&gt;Une&lt;/b&gt; faille.</description><pubDate>Thu, 15 Oct 2026 00:00:00 +0000</pubDate></item>
<item><title>Sans date</title><link>https://cert/3</link></item>
</channel></rss>"""


def test_flux_cert_fr_garde_la_semaine():
    items = v.parse_cert_feed(RSS, NOW - timedelta(days=7))
    assert [i["lien"] for i in items] == ["https://cert/2"]
    assert items[0]["titre"].startswith("Vulnérabilité dans LibreNMS") and items[0]["texte"] == "[Mise à jour] Une faille."
    with pytest.raises(v.NetError):
        v.parse_cert_feed(b"<html>302", NOW)


def test_kev_recent_et_illisible():
    kev = v.parse_kev({"vulnerabilities": [{"cveID": "CVE-1", "dateAdded": "2026-10-16"},
                                           {"cveID": "CVE-2", "dateAdded": "2026-01-01"}, {"x": 1}]})
    assert [e["cveID"] for e in v.kev_recent(kev, NOW - timedelta(days=7))] == ["CVE-1"]
    with pytest.raises(v.NetError):
        v.parse_kev({"oops": []})


def test_correspondance_produit_kev():
    tok = v.project_tokens("Dev/3cxmanager", {"paquets": {}})
    assert v.kev_matches_project({"vendorProject": "3CX", "product": "3CX Desktop App"}, tok)
    snipe = v.project_tokens("Dev/snipe-it", {"paquets": {("Packagist", "laravel/framework", "10.0.0"): False}})
    assert v.kev_matches_project({"vendorProject": "Grokability", "product": "Snipe-IT"}, snipe)
    assert v.kev_matches_project({"vendorProject": "Laravel", "product": "Laravel Framework"}, snipe)
    assert not v.kev_matches_project({"vendorProject": "Laravel", "product": "Livewire"}, snipe)
    assert not v.kev_matches_project({"vendorProject": "Microsoft", "product": "Multiple Products"}, snipe)
    assert v.title_mentions("Multiples vulnérabilités dans Snipe-IT (1 oct)", snipe)
    assert v.title_mentions("Vulnérabilité dans Laravel", snipe)
    assert not v.title_mentions("Vulnérabilité dans Microsoft Windows", snipe)


def test_score_cvss3():
    assert v._cvss3_score("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H") == 9.8
    assert v._cvss3_score("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:L/I:L/A:N") == 7.2
    assert v._cvss3_score("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:N") == 0.0
    assert v._cvss3_score("CVSS:3.1/AV:N") is None
    assert v.severity_rank({"database_specific": {"severity": "MODERATE"}}) == 2
    assert v.severity_rank({"severity": [{"type": "CVSS_V3", "score": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"}]}) == 4


# ---------------------------------------------------------------- fichiers de verrouillage

def test_package_lock_v3_et_v1():
    v3 = json.dumps({"lockfileVersion": 3, "packages": {
        "": {"name": "app", "dependencies": {"axios": "^1"}},
        "node_modules/axios": {"version": "1.6.0"},
        "node_modules/a/node_modules/lodash": {"version": "4.17.20", "dev": True},
        "node_modules/mon-lien": {"link": True, "resolved": "../x"},
        "node_modules/git-dep": {"version": "git+ssh://x"}}})
    assert v.parse_package_lock(v3) == {("npm", "axios", "1.6.0"): False, ("npm", "lodash", "4.17.20"): True}
    v1 = json.dumps({"lockfileVersion": 1, "dependencies": {
        "express": {"version": "4.17.1", "dependencies": {"qs": {"version": "6.7.0"}}}}})
    assert set(v.parse_package_lock(v1)) == {("npm", "express", "4.17.1"), ("npm", "qs", "6.7.0")}


def test_composer_requirements_poetry():
    composer = json.dumps({"packages": [{"name": "laravel/framework", "version": "v10.1.0"},
                                        {"name": "x/y", "version": "dev-master"}],
                           "packages-dev": [{"name": "phpunit/phpunit", "version": "10.0.1"}]})
    assert v.parse_composer_lock(composer) == {("Packagist", "laravel/framework", "10.1.0"): False,
                                               ("Packagist", "phpunit/phpunit", "10.0.1"): True}
    req = ("# commentaire\n-i https://moi:SECRET@index.exemple/simple\nDjango==4.2.1 ; python_version > '3'\n"
           "requests>=2.0\nfoo[bar]==1.0  # note\ngit+https://x/y.git\npkg @ https://moi:SECRET@x/p.whl\n")
    got = v.parse_requirements(req)
    assert got == {("PyPI", "django", "4.2.1"): False, ("PyPI", "foo", "1.0"): False}
    assert "SECRET" not in repr(got)
    poetry = '[[package]]\nname = "Jinja2"\nversion = "2.10"\ncategory = "main"\n'
    assert v.parse_poetry_lock(poetry) == {("PyPI", "jinja2", "2.10"): False}


def test_pnpm_trois_formats():
    v9 = ("lockfileVersion: '9.0'\nimporters:\n  .:\n    dependencies:\n      x:\n        version: 1.0.0\n"
          "packages:\n  '@babel/core@7.24.0':\n    resolution: {}\n  next@14.1.0:\n    resolution: {}\n"
          "snapshots:\n  next@14.1.0(react@18.2.0):\n    dependencies: {}\n  mon-lien@link:../x:\n    x: 1\n")
    assert set(v.parse_pnpm_lock(v9)) == {("npm", "@babel/core", "7.24.0"), ("npm", "next", "14.1.0")}
    v6 = "lockfileVersion: '6.0'\npackages:\n  /@types/node@20.1.0:\n    resolution: {}\n"
    assert set(v.parse_pnpm_lock(v6)) == {("npm", "@types/node", "20.1.0")}
    v5 = "lockfileVersion: 5.4\npackages:\n  /@scope/pkg/1.2.3_react@18:\n    resolution: {}\n  /lodash/4.17.21:\n    x: 1\n"
    assert set(v.parse_pnpm_lock(v5)) == {("npm", "@scope/pkg", "1.2.3"), ("npm", "lodash", "4.17.21")}


def test_yarn_v1_et_berry():
    v1 = ('# yarn lockfile v1\n\n"@babel/code-frame@^7.0.0", "@babel/code-frame@^7.10.4":\n  version "7.12.13"\n'
          '  resolved "https://registry/x"\n  dependencies:\n    foo "^1"\n\nlodash@^4.17.0:\n  version "4.17.21"\n')
    assert set(v.parse_yarn_lock(v1)) == {("npm", "@babel/code-frame", "7.12.13"), ("npm", "lodash", "4.17.21")}
    berry = ('__metadata:\n  version: 6\n\n"lodash@npm:^4.17.0":\n  version: 4.17.21\n  resolution: "x"\n\n'
             '"resolve@patch:resolve@npm%3A^1.0#~builtin":\n  version: 1.22.0\n')
    assert set(v.parse_yarn_lock(berry)) == {("npm", "lodash", "4.17.21")}


def _arbre(tmp_path):
    dev, divers = tmp_path / "Dev", tmp_path / "Divers"
    lock = lambda deps: json.dumps({"lockfileVersion": 3, "packages": {  # noqa: E731
        "": {}, **{f"node_modules/{n}": {"version": ver} for n, ver in deps.items()}}})
    (dev / "snipe-it").mkdir(parents=True)
    (dev / "snipe-it" / "package-lock.json").write_text(lock({"axios": "1.6.0"}))
    (dev / "snipe-it" / ".env").write_text("APP_KEY=ultra-secret\n")
    (dev / "snipe-it" / "node_modules" / "z").mkdir(parents=True)
    (dev / "snipe-it" / "node_modules" / "z" / "package-lock.json").write_text(lock({"cache": "6.6.6"}))
    (dev / "snipe-it" / "vendor" / "w").mkdir(parents=True)
    (dev / "snipe-it" / "vendor" / "w" / "composer.lock").write_text("{}")
    (dev / "gestsup").mkdir()
    (divers / "Studio" / "Astrel" / "hud").mkdir(parents=True)
    (divers / "Studio" / "Astrel" / "requirements.txt").write_text("jinja2==2.10\n")
    (divers / "Studio" / "Astrel" / "hud" / "package-lock.json").write_text(lock({"vite": "5.0.0"}))
    (divers / "Studio" / "Astrel" / ".venv" / "lib").mkdir(parents=True)
    (divers / "Studio" / "Astrel" / ".venv" / "lib" / "requirements.txt").write_text("evil==6.6.6\n")
    (divers / "Studio" / "cerveau").mkdir(parents=True)
    (divers / "Studio" / "cerveau" / "requirements.txt").write_text("flask==2.0.0\n")
    (divers / "Boutique" / "web").mkdir(parents=True)
    (divers / "Boutique" / "package-lock.json").write_text(lock({"next": "14.0.0"}))
    (divers / "Boutique" / "web" / "package-lock.json").write_text(lock({"next": "14.0.0"}))
    (divers / "archives" / "dior").mkdir(parents=True)
    (divers / "archives" / "dior" / "package-lock.json").write_text(lock({"axios": "1.6.0"}))


def test_scan_ignore_dossiers_et_regroupe_par_projet(tmp_path):
    _arbre(tmp_path)
    projects, errors = v.scan_projects()
    assert errors == []
    assert set(projects) == {"Dev/snipe-it", "Dev/gestsup", "Divers/Studio/Astrel", "Divers/Studio/cerveau",
                             "Divers/Boutique", "Divers/archives/dior"}
    assert set(projects["Dev/snipe-it"]["paquets"]) == {("npm", "axios", "1.6.0")}   # ni node_modules ni vendor
    assert set(projects["Divers/Studio/Astrel"]["paquets"]) == {("PyPI", "jinja2", "2.10"), ("npm", "vite", "5.0.0")}
    assert projects["Dev/gestsup"]["fichiers"] == []                     # reconnu par son nom seulement
    assert projects["Divers/archives/dior"]["archive"] and not projects["Divers/Boutique"]["archive"]
    found = [p.name for root in v.ROOTS.values() for p in v.find_lockfiles(root)]
    assert ".env" not in found


# ---------------------------------------------------------------- réseau simulé de bout en bout

KEV = {"vulnerabilities": [
    {"cveID": "CVE-2023-0001", "vendorProject": "Axios", "product": "axios", "dateAdded": "2024-01-01",
     "vulnerabilityName": "vieille faille exploitée"},
    {"cveID": "CVE-2026-9999", "vendorProject": "GestSup", "product": "GestSup", "dateAdded": "2026-10-17",
     "vulnerabilityName": "GestSup injection"},
    {"cveID": "CVE-2026-8888", "vendorProject": "Microsoft", "product": "Windows", "dateAdded": "2026-10-16",
     "vulnerabilityName": "Windows"}]}
VULNS = {
    "GHSA-axio": {"id": "GHSA-axio", "aliases": ["CVE-2023-0001"], "summary": "SSRF dans axios",
                  "database_specific": {"severity": "MODERATE"},
                  "affected": [{"package": {"ecosystem": "npm", "name": "axios"},
                                "ranges": [{"type": "SEMVER", "events": [{"introduced": "0"}, {"fixed": "1.6.8"}]}]}]},
    "GHSA-next": {"id": "GHSA-next", "aliases": ["CVE-2026-1111"], "summary": "Contournement du middleware",
                  "database_specific": {"severity": "CRITICAL"},
                  "affected": [{"package": {"ecosystem": "npm", "name": "next"},
                                "ranges": [{"type": "SEMVER", "events": [{"introduced": "13.0.0"}, {"fixed": "14.2.25"},
                                                                         {"introduced": "15.0.0"}, {"fixed": "15.2.3"}]}]}]},
    "PYSEC-next": {"id": "PYSEC-next", "aliases": ["CVE-2026-1111"], "summary": "doublon",
                   "database_specific": {"severity": "CRITICAL"}, "affected": []},
    "GHSA-jinja": {"id": "GHSA-jinja", "aliases": [], "summary": "faille basse",
                   "database_specific": {"severity": "LOW"}, "affected": []},
}
OSV_HITS = {("npm", "axios"): ["GHSA-axio"], ("npm", "next"): ["GHSA-next", "PYSEC-next"],
            ("PyPI", "jinja2"): ["GHSA-jinja"]}


def _faux_reseau(monkeypatch, sent: list, down: set = frozenset()):
    def fake(url, data=None, timeout=v.TIMEOUT_S):
        if any(url.startswith(d) for d in down):
            raise v.NetError(f"{v._host(url)} injoignable (simulé)")
        if url == v.KEV_URL:
            return json.dumps(KEV).encode()
        if url in v.CERT_FEEDS.values():
            return RSS
        if url == v.GITHUB_URL:
            return json.dumps([{"ghsa_id": "GHSA-g", "cve_id": "CVE-2026-2", "published_at": "2026-10-18T10:00:00Z",
                                "summary": "RCE", "html_url": "https://github.com/advisories/GHSA-g",
                                "vulnerabilities": [{"package": {"ecosystem": "npm", "name": "x"},
                                                     "first_patched_version": "2.0.0"}]}]).encode()
        if url == v.OSV_BATCH_URL:
            body = json.loads(data)
            sent.extend(body["queries"])
            return json.dumps({"results": [
                {"vulns": [{"id": i, "modified": "2026-10-01T00:00:00Z"}
                           for i in OSV_HITS.get((q["package"]["ecosystem"], q["package"]["name"]), [])]}
                for q in body["queries"]]}).encode()
        if url.startswith(v.OSV_VULN_URL):
            return json.dumps(VULNS[url.rsplit("/", 1)[1]]).encode()
        raise AssertionError(url)
    monkeypatch.setattr(v, "_get", fake)


def test_run_rapport_resume_et_etat(tmp_path, monkeypatch, capsys):
    _arbre(tmp_path)
    sent: list = []
    _faux_reseau(monkeypatch, sent)
    assert v.cmd_run(NOW) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1].startswith("RESUME: ") and out[-2].startswith("RAPPORT_TXT: ") and out[-3].startswith("RAPPORT: ")
    assert out[-2] == f"RAPPORT_TXT: {v.OUT_DIR / '2026-10-19.txt'}"
    assert out[-3] == f"RAPPORT: {v.OUT_DIR / '2026-10-19.txt'}"    # sans Chrome : repli sur le .txt
    assert out[-4].startswith("AVERTISSEMENT: PDF non produit (Chrome ou Chromium introuvable)")
    resume = out[-1][len("RESUME: "):]
    assert len(resume) <= v.RESUME_MAX and "*" not in resume and "#" not in resume
    assert "snipe-it" in resume and "axios 1.6.0 vers 1.6.8" in resume and "exploitée" in resume
    assert "Boutique" in resume and "next 14.0.0 vers 14.2.25" in resume
    assert "gestsup (CVE-2026-9999)" in resume
    assert "Archives : 1 projet(s)" in resume and "dior" not in resume
    # seuls écosystème, nom, version partent vers OSV
    assert sent and all(set(q) == {"package", "version"} and set(q["package"]) == {"ecosystem", "name"} for q in sent)
    assert not any(q["package"]["name"] in ("cache", "evil") for q in sent)
    report = (v.OUT_DIR / "2026-10-19.txt").read_text()
    assert "ultra-secret" not in report and "jinja" not in report   # faille basse non rapportée
    assert report.count("CVE-2026-1111") == 1                        # alias GHSA/PYSEC dédoublonnés (Boutique)
    assert "Windows" in report and "LibreNMS" in report
    state = json.loads(v.STATE_FILE.read_text())
    assert state["dernier_rapport"] == NOW.isoformat(timespec="seconds")
    assert not v.report_due(NOW + timedelta(days=1))
    assert "Dev/snipe-it|npm:axios|CVE-2023-0001" in state["signalees"]


def test_alerte_dedoublonnee(tmp_path, monkeypatch, capsys):
    _arbre(tmp_path)
    _faux_reseau(monkeypatch, [])
    assert v.cmd_alert(NOW) == 0
    first = capsys.readouterr().out.strip().splitlines()[-1]
    assert first.startswith("RESUME: ALERTE") and "snipe-it" in first and "CVE-2023-0001" in first
    assert "gestsup" in first and "dior" not in first and "Windows" not in first
    assert v.cmd_alert(NOW + timedelta(hours=7)) == 0
    assert capsys.readouterr().out.strip() == "RIEN"
    assert not v.alert_due(NOW + timedelta(hours=8))


def test_run_puis_alerte_ne_resignale_pas(tmp_path, monkeypatch, capsys):
    _arbre(tmp_path)
    _faux_reseau(monkeypatch, [])
    v.cmd_run(NOW)
    capsys.readouterr()
    v.cmd_alert(NOW + timedelta(hours=1))
    assert capsys.readouterr().out.strip() == "RIEN"


@pytest.mark.parametrize("down", [v.KEV_URL, v.OSV_BATCH_URL, v.OSV_VULN_URL])
def test_reseau_en_echec_rien_marque(tmp_path, monkeypatch, capsys, down):
    _arbre(tmp_path)
    _faux_reseau(monkeypatch, [], down={down})
    assert v.cmd_run(NOW) == 2
    assert v.cmd_alert(NOW) == 2
    out = capsys.readouterr().out
    assert out.count("ERREUR:") == 2 and "RESUME" not in out and "Traceback" not in out
    assert not v.STATE_FILE.exists()
    assert v.report_due(NOW) and v.alert_due(NOW)


def test_sources_secondaires_en_echec_le_rapport_part_quand_meme(tmp_path, monkeypatch, capsys):
    _arbre(tmp_path)
    _faux_reseau(monkeypatch, [], down={"https://www.cert.ssi.gouv.fr", "https://api.github.com"})
    assert v.cmd_run(NOW) == 0
    assert capsys.readouterr().out.strip().splitlines()[-1].startswith("RESUME: ")
    assert "échec : www.cert.ssi.gouv.fr injoignable" in (v.OUT_DIR / "2026-10-19.txt").read_text()


def test_get_reel_transforme_les_erreurs(monkeypatch):
    """`_get` ne plante pas : toute erreur réseau devient NetError, sans l'URL complète."""
    import urllib.request

    def boom(*_a, **_k):
        raise OSError("connexion refusée")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(v.NetError, match="api.osv.dev injoignable") as err:
        REAL_GET("https://api.osv.dev/v1/querybatch?x=1", b"{}")
    assert "querybatch" not in str(err.value)


# ---------------------------------------------------------------- gazette PDF (19/10)

def _collecte(tmp_path, monkeypatch):
    _arbre(tmp_path)
    _faux_reseau(monkeypatch, [])
    return v.collect(NOW)


def test_gazette_sections_et_ordre(tmp_path, monkeypatch):
    page = v.build_gazette_html(_collecte(tmp_path, monkeypatch), "2026-10-19.txt")
    assert "La Gazette sécurité d'Astrel" in page and "Lundi 19 octobre 2026" in page
    assert "Édition n° 43" in page                                     # numéro = semaine ISO
    assert "À corriger en priorité" in page and "Dans l'actualité" in page
    prio = page.split("À corriger en priorité", 1)[1].split("Les projets touchés", 1)[0]
    assert prio.index("axios") < prio.index("next")                     # exploitée avant critique
    assert "<b>axios</b> 1.6.0 → <b>1.6.8</b>" in prio and "exploitée" in prio
    assert page.index("Tes projets (Divers)") < page.index("Travail (Dev)")  # Divers puis Dev
    assert ">Boutique</h4>" in page and ">snipe-it</h4>" in page
    assert "LibreNMS : une faille" in page                              # titre CERT-FR raccourci
    assert "GestSup : faille activement exploitée" in page              # ajout KEV, nom de projet signalé
    assert "concerne gestsup" in page
    assert '<a class="url" href="https://cert/2">cert/2</a>' in page    # lien affiché en texte
    assert "2026-10-19.txt" in page and "Archives." in page and "dior" in page
    assert "jinja" not in page                                          # faille basse : absente comme du .txt


def test_gazette_echappe_les_donnees_des_flux(tmp_path, monkeypatch):
    c = _collecte(tmp_path, monkeypatch)
    evil = '<script>alert(1)</script><img src=x onerror=alert(2)>"\''
    c["cert"]["alerte"] = [{"titre": "Vulnérabilité dans " + evil, "lien": "javascript:alert(3)",
                            "date": "2026-10-18", "texte": evil}]
    c["cert"]["avis"] = [{"titre": evil, "lien": 'https://x.fr/"><script>', "date": "2026-10-18", "texte": ""}]
    c["github"] = [{"id": "GHSA-1", "cve": "", "date": "2026-10-18", "titre": evil, "paquets": [evil],
                    "lien": "https://github.com/advisories/GHSA-1"}]
    c["kev_recent"] = [{"cveID": "CVE-1\"><script>", "vendorProject": evil, "product": "x", "dateAdded": "2026-10-18",
                        "vulnerabilityName": evil}]
    c["sources"]["<b>pirate</b>"] = evil
    rows = c["findings"]["Divers/Boutique"]
    rows[0] = {**rows[0], "paquet": evil, "version": evil, "correctif": evil}
    c["projects"]['Divers/<i onclick="x">'] = c["projects"].pop("Divers/Boutique")
    c["findings"]['Divers/<i onclick="x">'] = c["findings"].pop("Divers/Boutique")
    page = v.build_gazette_html(c, '<x>.txt')
    for bad in ("<script", "<img", '<i onclick', "javascript:", "<b>pirate", "<x>", "x.fr/&quot;"):
        assert bad not in page, bad
    tags = re.findall(r"<[a-zA-Z][^>]*>", page)                       # balises réellement présentes
    assert not [t for t in tags if re.search(r"\s(on\w+|src|style)\s*=", t)]
    assert {re.match(r"<(\w+)", t).group(1).lower() for t in tags} <= {
        "html", "head", "meta", "title", "style", "body", "header", "h1", "h2", "h3", "h4", "div", "span", "b",
        "section", "p", "ul", "li", "br", "a", "footer"}
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "default-src &#x27;none&#x27;" not in page and "default-src 'none'" in page   # CSP posée
    assert "@import" not in page and "url(" not in page and "@font-face" not in page   # rien de distant
    assert 'href="https://github.com/advisories/GHSA-1"' in page
    assert page.count("<a ") == page.count('href="https://')


def test_titres_courts_en_francais():
    assert v.cert_title_fr("Multiples vulnérabilités dans les produits F5 (15 octobre 2026)") == "Produits F5 : plusieurs failles"
    assert v.cert_title_fr("Vulnérabilité dans Ivanti Connect Secure (ICS) (14 octobre 2026)") == \
        "Ivanti Connect Secure (ICS) : une faille"
    assert v.cert_title_fr("Multiples vulnérabilités dans le noyau Linux d'Ubuntu (16 octobre 2026)") == \
        "Noyau Linux d'Ubuntu : plusieurs failles"
    assert v.kev_title_fr({"vendorProject": "Fortinet", "product": "Multiple Products",
                           "vulnerabilityName": "Fortinet Multiple Products Path Traversal Vulnerability"}) == \
        "Fortinet (plusieurs produits) : accès à des fichiers hors du dossier prévu"
    assert v.kev_title_fr({"vendorProject": "Microsoft", "product": "Windows",
                           "vulnerabilityName": "Microsoft Windows Command Injection Vulnerability"}) == \
        "Microsoft Windows : injection de commandes"
    assert v.github_title_fr({"titre": "LMCache: Remote code execution via unsafe deserialization", "paquets": []}) == \
        "LMCache : exécution de code à distance"
    assert v.github_title_fr({"titre": "Microsoft Security Advisory CVE-1 - ASP.NET Core Security Feature Bypass "
                              "Vulnerability", "paquets": ["nuget:x"]}) == "ASP.NET Core : contournement d'une protection"
    assert v.github_title_fr({"titre": "", "paquets": ["npm:lodash (corrigé en 1.0)"]}) == "lodash : faille critique"


def _faux_chrome(tmp_path, body: str) -> str:
    """Un « Chrome » exécutable qui note la page reçue puis exécute `body`."""
    return programme_python(tmp_path / "faux-chrome", "import sys, pathlib, urllib.parse, urllib.request\n"
                            "args = sys.argv[1:]\n"
                            "out = next(a.split('=', 1)[1] for a in args if a.startswith('--print-to-pdf='))\n"
                            # url2pathname : « /C:/… » d'une adresse file: redevient « C:\\… » sous Windows (03/11)
                            "page = pathlib.Path(urllib.request.url2pathname(urllib.parse.urlparse(args[-1]).path))\n"
                            f"pathlib.Path({str(tmp_path / 'vu.txt')!r}).write_text(str(page) + '\\n' + ' '.join(args[:-1]))\n"
                            + body)


def test_run_produit_le_pdf_et_efface_le_html(tmp_path, monkeypatch, capsys):
    exe = _faux_chrome(tmp_path, "pathlib.Path(out).write_bytes(b'%PDF-1.4\\n/Type /Page\\n/Type /Pages\\n')\n")
    monkeypatch.setattr(v, "find_chrome", lambda: exe)
    _arbre(tmp_path)
    _faux_reseau(monkeypatch, [])
    assert v.cmd_run(NOW) == 0
    out = capsys.readouterr().out.strip().splitlines()
    pdf, txt = v.OUT_DIR / "2026-10-19.pdf", v.OUT_DIR / "2026-10-19.txt"
    assert out[-3:-1] == [f"RAPPORT: {pdf}", f"RAPPORT_TXT: {txt}"] and out[-1].startswith("RESUME: ")
    assert not any(line.startswith("AVERTISSEMENT") for line in out)
    assert pdf.read_bytes().startswith(b"%PDF-") and txt.is_file()
    page, args = (tmp_path / "vu.txt").read_text().split("\n", 1)
    assert not v.Path(page).exists() and not list(tmp_path.glob("astrel-gazette-*"))   # HTML et profil effacés
    assert "--headless" in args and "--no-sandbox" in args and "--disable-gpu" in args
    assert v.pdf_pages(pdf) == 1


@pytest.mark.parametrize("body, raison", [
    ("sys.exit(1)\n", "Chrome n'a pas produit de PDF (code 1)"),                         # plante sans rien écrire
    ("pathlib.Path(out).write_text('<html>pas un pdf')\n", "Chrome n'a pas produit de PDF (code 0)"),
])
def test_chrome_en_echec_repli_sur_le_txt(tmp_path, monkeypatch, capsys, body, raison):
    exe = _faux_chrome(tmp_path, body)
    monkeypatch.setattr(v, "find_chrome", lambda: exe)
    _arbre(tmp_path)
    _faux_reseau(monkeypatch, [])
    assert v.cmd_run(NOW) == 0
    out = capsys.readouterr().out
    assert f"AVERTISSEMENT: PDF non produit ({raison}), rapport texte seul." in out
    assert f"RAPPORT: {v.OUT_DIR / '2026-10-19.txt'}" in out and "Traceback" not in out
    assert not (v.OUT_DIR / "2026-10-19.pdf").exists() and not list(tmp_path.glob("astrel-gazette-*"))
    assert json.loads(v.STATE_FILE.read_text())["dernier_rapport"]   # la semaine est faite quand même


def test_chrome_qui_ne_rend_pas_la_main(tmp_path, monkeypatch):
    exe = _faux_chrome(tmp_path, "import time; time.sleep(30)\n")
    monkeypatch.setattr(v, "find_chrome", lambda: exe)
    monkeypatch.setattr(v, "CHROME_TIMEOUT_S", 1)
    assert v.html_to_pdf("<p>x</p>", tmp_path / "x.pdf") == "Chrome n'a pas fini en 1 s"
    assert not (tmp_path / "x.pdf").exists()


def test_erreur_inattendue_jamais_de_plantage(tmp_path, monkeypatch):
    def boom(*_a, **_k):
        raise KeyError("champ absent")
    monkeypatch.setattr(v, "build_gazette_html", boom)
    assert v.write_gazette({}, tmp_path / "r.txt") == (None, "PDF non produit (erreur inattendue (KeyError)), rapport texte seul.")


def test_trop_de_pages_seconde_impression_compacte(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(v, "build_gazette_html", lambda c, name, compact=False: calls.append(compact) or "<p>x</p>")
    monkeypatch.setattr(v, "html_to_pdf", lambda page, pdf: pdf.write_bytes(b"%PDF-") and None)
    monkeypatch.setattr(v, "pdf_pages", lambda p: 5)
    assert v.write_gazette({}, tmp_path / "r.txt") == (tmp_path / "r.pdf", "")
    assert calls == [False, True]


@pytest.mark.skipif(REAL_CHROME is None, reason="Chrome/Chromium absent de cette machine")
def test_vrai_chrome_imprime_la_gazette(tmp_path, monkeypatch):
    monkeypatch.setattr(v, "find_chrome", lambda: REAL_CHROME)
    pdf, warning = v.write_gazette(_collecte(tmp_path, monkeypatch), v.OUT_DIR / "2026-10-19.txt")
    assert warning == "" and pdf.read_bytes().startswith(b"%PDF-")
    assert 1 <= v.pdf_pages(pdf) <= v.PDF_MAX_PAGES
    assert not list(tmp_path.glob("astrel-gazette-*"))
