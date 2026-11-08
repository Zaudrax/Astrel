"""Installation et mise à jour multiplateformes (P2, lot 2) : contrôle de la
machine, installateur, mise à jour. Rien n'est installé pour de vrai : chaque
morceau est essayé dans un dossier temporaire."""
import chemins_machine  # chemins de la machine qui lance la suite
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # les dataclasses retrouvent leur module
    spec.loader.exec_module(mod)
    return mod


inst = _load("astrel_install", "install.py")
upd = _load("astrel_update", "update.py")
chk = _load("check_install", "tools/check_install.py")
fetch = _load("fetch_models", "tools/fetch_models.py")


# --- réglages ---

def test_exemples_presents_et_valides():
    for name in inst.EXAMPLES:
        data = json.loads((ROOT / "config" / f"{name}.example.json").read_text(encoding="utf-8"))
        assert data


def test_exemples_lus_par_astrel(monkeypatch):
    from astrel import channels, micprofile
    from astrel.senses import reflex

    monkeypatch.setattr(micprofile, "FILE", ROOT / "config" / "mic_profiles.example.json")
    prof = micprofile.resolve("alsa_input.usb-micro", "Micro USB")
    assert (prof.gain, prof.vad, prof.wake, prof.key) == (2.0, 0.35, 0.4, "")
    monkeypatch.setattr(reflex, "RULES_FILE", ROOT / "config" / "reflexes.example.json")
    assert [r["id"] for r in reflex.load_rules()] == [r["id"] for r in reflex.DEFAULT_RULES]
    items, default = channels.parse(json.loads((ROOT / "config" / "channels.example.json").read_text("utf-8")))
    assert default == "general" and [c.id for c in items] == ["general", "exemple", "local"]
    assert [c.engine for c in items] == ["", "", "local"]  # 04/11 : canal du moteur local


def test_exemples_copies_sans_ecraser(tmp_path):
    (tmp_path / "config").mkdir()
    for name in inst.EXAMPLES:
        (tmp_path / "config" / f"{name}.example.json").write_text('{"exemple": 1}')
    (tmp_path / "config" / "channels.json").write_text('{"a moi": 1}')
    assert inst.copy_examples(tmp_path) == ["reflexes.json", "mic_profiles.json"]
    assert json.loads((tmp_path / "config" / "channels.json").read_text()) == {"a moi": 1}
    assert inst.copy_examples(tmp_path) == []


def test_prudent_seulement_a_la_premiere_installation(tmp_path):
    assert not inst.initial_autonomy(tmp_path, fresh=False)
    assert not (tmp_path / "runtime" / "autonomy.json").exists()
    assert inst.initial_autonomy(tmp_path, fresh=True)
    assert json.loads((tmp_path / "runtime" / "autonomy.json").read_text()) == {"mode": "prudent"}
    (tmp_path / "runtime" / "autonomy.json").write_text('{"mode": "autonome"}')
    assert not inst.initial_autonomy(tmp_path, fresh=True)  # le choix de l'utilisateur reste
    assert "autonome" in (tmp_path / "runtime" / "autonomy.json").read_text()


def test_affichage_simple_seulement_a_la_premiere_installation(tmp_path):
    """22/10 (plan P23, choix de l'utilisateur) : Simple chez un autre (première installation), Avancé
    chez lui (le défaut du réglage, `astrel/settings.py`) ; le choix déjà fait n'est jamais touché."""
    f = tmp_path / "runtime" / "settings.json"
    assert not inst.initial_affichage(tmp_path, fresh=False) and not f.exists()
    assert inst.initial_affichage(tmp_path, fresh=True)
    assert json.loads(f.read_text()) == {"values": {"affichage": "simple"}}
    f.write_text(json.dumps({"values": {"affichage": "avance", "theme": "premium"}}))
    assert not inst.initial_affichage(tmp_path, fresh=True)  # le choix de l'utilisateur reste
    assert json.loads(f.read_text())["values"] == {"affichage": "avance", "theme": "premium"}
    f.write_text(json.dumps({"values": {"theme": "premium"}}))
    assert inst.initial_affichage(tmp_path, fresh=True)       # autres réglages gardés
    assert json.loads(f.read_text())["values"] == {"theme": "premium", "affichage": "simple"}
    f.write_text("{abîmé")
    assert not inst.initial_affichage(tmp_path, fresh=True) and f.read_text() == "{abîmé"


def test_service_garde_le_dossier_des_projets(tmp_path):
    unit = tmp_path / "astrel.service"
    unit.write_text("[Service]\nEnvironment=ASTREL_WORKDIR=/srv/mes projets\n")
    assert inst.current_workdir(unit) == "/srv/mes projets"
    unit.write_text("Environment=ASTREL_WORKDIR=@WORKDIR@\n")
    assert inst.current_workdir(unit) == ""
    assert inst.current_workdir(tmp_path / "absent") == ""


def test_modele_du_service_sans_chemin_personnel():
    text = (ROOT / "systemd" / "astrel.service").read_text(encoding="utf-8")
    assert f"{chemins_machine.PROJETS}" not in text and "@WORKDIR@" in text
    out = inst.render(text, ROOT="/opt/astrel", PATH="/usr/bin", WORKDIR="/home/ami/Projets")
    assert "@" not in out.replace("@.service", "")
    assert "Environment=ASTREL_WORKDIR=/home/ami/Projets" in out


def test_python_de_la_venv_par_systeme(tmp_path):
    assert inst.venv_python(tmp_path, "windows") == tmp_path / ".venv" / "Scripts" / "python.exe"
    assert inst.venv_python(tmp_path, "macos") == tmp_path / ".venv" / "bin" / "python"


# --- contrôle de la machine ---

def test_controle_rend_des_lignes_et_un_rapport_anonyme(monkeypatch):
    results = chk.run_checks(ci=True)
    assert results and all(c.status in (chk.OK, chk.MANQUE, chk.ALERTE, chk.INFO) for c in results)
    text = chk.report([chk.Check("x", chk.INFO, f"{Path.home()}/a, ecrire a prenom.nom@exemple.fr")])
    assert str(Path.home()) not in text and "@exemple.fr" not in text and "~/a" in text


def test_python_trop_ancien(monkeypatch):
    monkeypatch.setattr(chk.sys, "version_info", (3, 10, 12))
    assert chk.check_python("linux").status == chk.MANQUE
    monkeypatch.setattr(chk.sys, "version_info", (3, 15, 0))
    assert chk.check_python("linux").status == chk.ALERTE


def test_claude_absent_bloque_hors_ci(monkeypatch):
    monkeypatch.setattr(chk, "_out", lambda cmd, timeout=15.0: (127, ""))
    assert chk.check_claude("linux", ci=False)[0].status == chk.MANQUE
    assert chk.check_claude("linux", ci=True)[0].status == chk.INFO


def test_connexion_claude_sans_donnee_personnelle(monkeypatch):
    status = {"loggedIn": True, "authMethod": "claude.ai", "email": "a@b.fr", "orgId": "x", "subscriptionType": "pro"}

    def fake(cmd, timeout=15.0):
        return (0, "2.1.285 (Claude Code)") if "--version" in cmd else (0, json.dumps(status))

    monkeypatch.setattr(chk, "_out", fake)
    res = chk.check_claude("linux", ci=False)
    assert [c.status for c in res] == [chk.OK, chk.OK]
    assert "a@b.fr" not in json.dumps([c.__dict__ for c in res]) and "pro" in res[1].detail


# --- mise à jour ---

def test_empreinte_des_dependances(tmp_path):
    (tmp_path / "requirements.txt").write_text("a==1\n")
    d1 = upd.digest(tmp_path, ["requirements.txt"])
    (tmp_path / "requirements.txt").write_text("a==2\n")
    assert upd.digest(tmp_path, ["requirements.txt"]) != d1


@pytest.mark.skipif(shutil.which("git") is None, reason="git absent")
def test_modifs_locales_et_fichiers_changes(tmp_path):
    def g(*a):
        subprocess.run(["git", *a], cwd=tmp_path, check=True, capture_output=True)

    g("init", "-q")
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    (tmp_path / "f.txt").write_text("1")
    g("add", "f.txt")
    g("commit", "-q", "-m", "1")
    rev1 = upd.git("rev-parse", "HEAD", root=tmp_path).stdout.strip()
    (tmp_path / "nouveau.txt").write_text("non suivi")
    assert upd.local_changes(tmp_path) == []  # un fichier non suivi ne gêne pas
    (tmp_path / "f.txt").write_text("2")
    assert upd.local_changes(tmp_path) == ["f.txt"]
    g("commit", "-q", "-am", "2")
    rev2 = upd.git("rev-parse", "HEAD", root=tmp_path).stdout.strip()
    assert upd.changed_files(rev1, rev2, root=tmp_path) == ["f.txt"]
    assert upd.changed_files(rev2, rev2, root=tmp_path) == []


def test_modeles_deja_la_non_retelecharges(tmp_path, monkeypatch):
    dest = tmp_path / "m.onnx"
    dest.write_bytes(b"x")
    monkeypatch.setattr(fetch.urllib.request, "urlopen", lambda *a, **k: pytest.fail("téléchargé"))
    fetch.get("https://exemple.invalid/m.onnx", dest)


def test_installation_prepare_le_pont_whatsapp(monkeypatch, capsys):
    """18/10 (plan P5) : `npm ci` aussi dans whatsapp/ (un ami n'a pas de node_modules,
    le pont serait introuvable) ; un échec n'arrête pas l'installation."""
    calls = []

    def fake_run(cmd, cwd=inst.ROOT, check=True):
        calls.append((list(map(str, cmd)), Path(cwd).name))
        return 1 if Path(cwd).name == "whatsapp" else 0

    class Check:
        MANQUE, ALERTE = "MANQUE", "ALERTE"

        @staticmethod
        def run_checks():
            return []

    monkeypatch.setattr(inst, "run", fake_run)
    monkeypatch.setattr(inst, "load_check_install", lambda root=inst.ROOT: Check)
    monkeypatch.setattr(inst.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(inst, "initial_autonomy", lambda **k: False)
    monkeypatch.setattr(inst, "initial_affichage", lambda **k: False)  # jamais le vrai runtime/settings.json
    monkeypatch.setattr(inst, "copy_examples", lambda: [])
    monkeypatch.setattr(inst, "preparer_projets", lambda workdir: False)  # jamais le vrai dossier personnel
    assert inst.main(["--sans-modeles", "--sans-demarrage"]) == 0
    assert (["npm", "ci", "--no-audit", "--no-fund", "--silent"], "whatsapp") in calls
    assert "Préparer WhatsApp" in capsys.readouterr().out


def _installation_sans_rien(monkeypatch, code_de):
    """main() d'install.py sans rien lancer : (commande, dossier) de chaque appel retenus."""
    calls = []

    def fake_run(cmd, cwd=inst.ROOT, check=True):
        calls.append((list(map(str, cmd)), Path(cwd).name, check))
        return code_de(Path(cwd).name)

    class Check:
        MANQUE, ALERTE = "MANQUE", "ALERTE"

        @staticmethod
        def run_checks():
            return []

    monkeypatch.setattr(inst, "run", fake_run)
    monkeypatch.setattr(inst, "load_check_install", lambda root=inst.ROOT: Check)
    monkeypatch.setattr(inst.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(inst, "initial_autonomy", lambda **k: False)
    monkeypatch.setattr(inst, "initial_affichage", lambda **k: False)
    monkeypatch.setattr(inst, "copy_examples", lambda: [])
    monkeypatch.setattr(inst, "preparer_projets", lambda workdir: False)  # jamais le vrai dossier personnel
    assert inst.main(["--sans-modeles", "--sans-demarrage"]) == 0
    return calls


def test_installation_prepare_le_worker_des_images(monkeypatch, capsys):
    """05/11 (T45) : `npm ci` dans imagegen/ (playwright-core FIXÉ par le package-lock), sur les trois
    systèmes (rien ne dépend du système ici) ; un échec n'arrête pas l'installation."""
    assert json.loads((ROOT / "imagegen" / "package-lock.json").read_text(encoding="utf-8"))["packages"][
        "node_modules/playwright-core"]["version"] == json.loads(
        (ROOT / "imagegen" / "package.json").read_text(encoding="utf-8"))["dependencies"]["playwright-core"]
    for sys_name in ("linux", "macos", "windows"):
        monkeypatch.setattr(inst, "system", lambda s=sys_name: s)
        calls = _installation_sans_rien(monkeypatch, lambda nom: 0)
        assert (["npm", "ci", "--no-audit", "--no-fund", "--silent"], "imagegen", False) in calls, sys_name
    _installation_sans_rien(monkeypatch, lambda nom: 1 if nom == "imagegen" else 0)
    out = capsys.readouterr().out
    assert "images et vidéos IA non préparées" in out and "cd imagegen && npm ci" in out and "Installé." in out


def test_installation_sans_npm_dit_ce_qui_manque(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(inst, "run", lambda cmd, cwd=inst.ROOT, check=True: calls.append(Path(cwd).name) or 0)
    monkeypatch.setattr(inst, "load_check_install", lambda root=inst.ROOT: type(
        "C", (), {"MANQUE": "MANQUE", "ALERTE": "ALERTE", "run_checks": staticmethod(lambda: [])}))
    monkeypatch.setattr(inst.shutil, "which", lambda name: None if name == "npm" else f"/usr/bin/{name}")
    monkeypatch.setattr(inst, "initial_autonomy", lambda **k: False)
    monkeypatch.setattr(inst, "initial_affichage", lambda **k: False)
    monkeypatch.setattr(inst, "copy_examples", lambda: [])
    monkeypatch.setattr(inst, "preparer_projets", lambda workdir: False)  # jamais le vrai dossier personnel
    assert inst.main(["--sans-modeles", "--sans-demarrage"]) == 0
    assert not {"imagegen", "whatsapp", "mcp-servers"} & set(calls)
    assert "ni images et vidéos IA" in capsys.readouterr().out


def _faux_imagegen(tmp_path, attendu="1.64.1", installe=None):
    d = tmp_path / "imagegen"
    d.mkdir(parents=True)
    if attendu:
        (d / "package-lock.json").write_text(json.dumps(
            {"lockfileVersion": 3, "packages": {"node_modules/playwright-core": {"version": attendu}}}))
    if installe:
        (d / "node_modules" / "playwright-core").mkdir(parents=True)
        (d / "node_modules" / "playwright-core" / "package.json").write_text(json.dumps({"version": installe}))
    return tmp_path


def test_controle_du_worker_des_images(tmp_path):
    """05/11 (T45) : check_install vérifie imagegen/node_modules contre le package-lock (ALERTE seulement)."""
    ok = chk.check_imagegen(_faux_imagegen(tmp_path / "a", installe="1.64.1"))
    assert (ok.status, ok.detail) == (chk.OK, "playwright-core 1.64.1")
    absent = chk.check_imagegen(_faux_imagegen(tmp_path / "b"))
    assert absent.status == chk.ALERTE and absent.fix == "cd imagegen && npm ci"
    ecart = chk.check_imagegen(_faux_imagegen(tmp_path / "c", installe="1.50.0"))
    assert ecart.status == chk.ALERTE and "1.50.0 installé, 1.64.1 attendu" in ecart.detail
    sans_lock = chk.check_imagegen(_faux_imagegen(tmp_path / "d", attendu=None))
    assert sans_lock.status == chk.INFO
    (tmp_path / "b" / "imagegen" / "node_modules" / "playwright-core").mkdir(parents=True)
    (tmp_path / "b" / "imagegen" / "node_modules" / "playwright-core" / "package.json").write_text("{abîmé")
    assert chk.check_imagegen(tmp_path / "b").status == chk.ALERTE


def test_controle_apres_installation_cite_le_worker(monkeypatch):
    monkeypatch.setattr(chk, "venv_python", lambda: Path(sys.executable))
    noms = [c.name for c in chk.check_installed()]
    assert "images et vidéos IA (Node)" in noms


def test_mise_a_jour_reinstalle_les_dossiers_npm_ci():
    """05/11 (T45) : update.py suit aussi les package-lock de whatsapp/ et imagegen/ (npm ci)."""
    for nom in upd.NPM_CI:
        assert upd.WATCHED[nom] == [f"{nom}/package.json", f"{nom}/package-lock.json"]
        assert (ROOT / nom / "package-lock.json").is_file()


# --- icône du lanceur (22/10, plan P25) : jamais le vrai ~/.local, un HOME temporaire ---

def _faux_depot(tmp_path, choix=None):
    root = tmp_path / "depot"
    shutil.copytree(ROOT / "hud" / "assets" / "logo", root / "hud" / "assets" / "logo")
    shutil.copyfile(ROOT / "hud" / "assets" / "astrel-icon.png", root / "hud" / "assets" / "astrel-icon.png")
    (root / "hud" / "assets" / "logo" / "choix.txt").unlink(missing_ok=True)
    if choix:
        (root / "hud" / "assets" / "logo" / "choix.txt").write_text(choix + "\n")
    return root


def test_desktop_icone_astrel_hud():
    for name in ("astrel-hud.desktop",):  # 29/10 : la bulle n'a plus de .desktop (service systemd)
        text = (ROOT / "systemd" / name).read_text(encoding="utf-8")
        assert f"\nIcon={inst.ICON_NAME}\n" in text
        if shutil.which("desktop-file-validate"):
            out = subprocess.run(["desktop-file-validate", str(ROOT / "systemd" / name)], capture_output=True, text=True)
            assert out.returncode == 0 and not out.stdout.strip(), out.stdout
    assert "\nStartupWMClass=AstrelHUD\n" in (ROOT / "systemd" / "astrel-hud.desktop").read_text(encoding="utf-8")
    # P31 lot D : nom affiché Astrel, identifiants (fichiers, Icon, classe de fenêtre) inchangés
    assert "\nName=Astrel\n" in (ROOT / "systemd" / "astrel-hud.desktop").read_text(encoding="utf-8")
    assert not (ROOT / "systemd" / "astrel-bubble.desktop").exists()


def test_icones_de_l_etoile_astrel(tmp_path):
    """P31 lot D : la variante e (étoile) est acceptée et c'est le choix livré dans le dépôt."""
    assert (ROOT / "hud" / "assets" / "logo" / "choix.txt").read_text(encoding="utf-8").strip() == "e"
    root = _faux_depot(tmp_path, "e")
    icons = inst.launcher_icons(root)
    assert [d for d, _ in icons] == [f"{s}x{s}" for s in inst.ICON_SIZES] + ["scalable"]
    assert icons[-1][1].name == "astrel-logo-e.svg"


def test_icones_de_la_variante_choisie(tmp_path):
    root = _faux_depot(tmp_path, "D")
    icons = inst.launcher_icons(root)
    assert [d for d, _ in icons] == [f"{s}x{s}" for s in inst.ICON_SIZES] + ["scalable"]
    assert all(src.name.startswith("astrel-logo-d-") or src.name == "astrel-logo-d.svg" for _, src in icons)
    (root / "hud" / "assets" / "logo" / "choix.txt").write_text("z")  # choix illisible : repli
    assert inst.launcher_icons(root) == [("scalable", root / "hud" / "assets" / "astrel-icon.png")]
    (root / "hud" / "assets" / "astrel-icon.png").unlink()
    assert inst.launcher_icons(root) == []


@pytest.mark.skipif(sys.platform == "win32", reason="icônes du lanceur et uninstall.sh : Linux")
def test_icones_posees_puis_retirees_dans_un_home_temporaire(tmp_path, monkeypatch):
    from PIL import Image

    home = tmp_path / "home"
    calls = []
    monkeypatch.setattr(inst, "run", lambda cmd, cwd=inst.ROOT, check=True: calls.append(list(map(str, cmd))) or 0)
    base = home / ".local" / "share" / "icons" / "hicolor"
    # Sans choix : l'icône du HUD dans scalable (format png).
    written = inst.install_icons(_faux_depot(tmp_path), home)
    assert written == [base / "scalable" / "apps" / "astrel-hud.png"]
    # Choix fait : une taille par dossier, le SVG remplace le PNG du repli (sinon il le masquerait).
    shutil.rmtree(tmp_path / "depot")
    written = inst.install_icons(_faux_depot(tmp_path, "a"), home)
    assert len(written) == len(inst.ICON_SIZES) + 1
    for s in inst.ICON_SIZES:
        im = Image.open(base / f"{s}x{s}" / "apps" / "astrel-hud.png")
        assert im.size == (s, s) and im.mode == "RGBA"
    assert not (base / "scalable" / "apps" / "astrel-hud.png").exists()
    assert (base / "scalable" / "apps" / "astrel-hud.svg").read_text().startswith("<svg")
    if shutil.which("gtk-update-icon-cache") or shutil.which("gtk4-update-icon-cache"):
        assert calls and calls[-1][1:] == ["-q", "-f", "-t", str(base)]
    # uninstall.sh : seul le bloc des icônes est joué (le reste arrêterait le vrai service).
    text = (ROOT / "uninstall.sh").read_text(encoding="utf-8")
    block = text[text.index("# --- icône du lanceur"):text.index("# --- fin de l'icône ---")]
    (base / "48x48" / "apps" / "autre.png").write_bytes(b"x")  # icône d'une autre application
    out = subprocess.run(["bash", "-euo", "pipefail", "-c", block], env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert not list(base.glob("*/apps/astrel-hud.*"))
    assert (base / "48x48" / "apps" / "autre.png").exists()


def test_installation_linux_ecrit_le_lanceur_et_l_icone(tmp_path, monkeypatch):
    home = tmp_path / "home"
    calls = []
    monkeypatch.setattr(inst.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(inst, "run", lambda cmd, cwd=inst.ROOT, check=True: calls.append(list(map(str, cmd))) or 0)
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "")  # pas de raccourcis GNOME
    root = _faux_depot(tmp_path, "d")
    shutil.copytree(ROOT / "systemd", root / "systemd")
    ancien = home / ".config" / "autostart" / "astrel-bubble.desktop"  # lanceur d'avant le 29/10
    ancien.parent.mkdir(parents=True)
    ancien.write_text("[Desktop Entry]\nExec=x/bubble.sh\nX-GNOME-Autostart-enabled=true\n")
    inst.install_linux_autostart(root, "/srv/projets")
    desktop = (home / ".local" / "share" / "applications" / "astrel-hud.desktop").read_text()
    assert "Icon=astrel-hud" in desktop and f"Exec={root}/hud.sh" in desktop
    # 29/10 : la bulle démarre par SON service, l'ancien autostart est désactivé (deux bulles sinon)
    unit = (home / ".config" / "systemd" / "user" / "astrel-bubble.service").read_text()
    assert f"ExecStart={root}/.venv/bin/python -m astrel.bubble" in unit and "@ROOT@" not in unit
    assert ancien.read_text().count("X-GNOME-Autostart-enabled") == 1 and "Autostart-enabled=false" in ancien.read_text()
    assert ["systemctl", "--user", "enable", "astrel.service", "astrel-bubble.service"] in calls
    assert (home / ".local/share/icons/hicolor/48x48/apps/astrel-hud.png").is_file()
    assert ["systemctl", "--user", "daemon-reload"] in calls


def test_ci_lance_tous_les_tests_node():
    """05/11 (T45) : le job « linux » lance TOUS les tests Node (avant : seuls ceux que pytest relançait,
    7 fichiers du HUD sur 13, ni le pont WhatsApp ni le worker des images), après `npm ci` comme install.py."""
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    linux = ci[ci.index("\n  linux:"):ci.index("\n  portage:")]
    portage = ci[ci.index("\n  portage:"):]
    for dossier, cmd in (("hud", "node --test tests/*.test.mjs"), ("whatsapp", "node --test"),
                         ("imagegen", "node --test --test-concurrency=1")):
        assert f"working-directory: {dossier}\n        run: {cmd}\n" in linux, dossier
        assert any(ROOT.joinpath(dossier).glob("**/*.test.mjs")), dossier
    assert "(cd whatsapp && npm ci" in linux and "(cd imagegen && npm ci" in linux
    assert "run: node --test tests/*.test.mjs" in portage and "npm ci --no-audit --no-fund && node --test\n" in portage
    assert "actions/setup-node@v4" in portage and portage.count('node-version: "22"') == 1


def test_dossier_des_projets_cree_s_il_manque(tmp_path, monkeypatch, capsys):
    """07/11 (avant le premier essai sur un vrai Windows) : Claude Code est lancé DANS ce dossier ; un PC neuf
    n'a pas de « Projets », et le cerveau ne démarrait jamais. Créé à l'installation, même sans démarrage auto."""
    projets = tmp_path / "Mes projets" / "Projets"
    assert inst.preparer_projets(str(projets)) is True and projets.is_dir()
    assert inst.preparer_projets(str(projets)) is False  # déjà là : rien
    monkeypatch.setattr(inst, "run", lambda cmd, cwd=inst.ROOT, check=True: 0)
    monkeypatch.setattr(inst, "load_check_install", lambda root=inst.ROOT: type(
        "C", (), {"MANQUE": "MANQUE", "ALERTE": "ALERTE", "run_checks": staticmethod(lambda: [])}))
    monkeypatch.setattr(inst.shutil, "which", lambda name: None)
    monkeypatch.setattr(inst, "initial_autonomy", lambda **k: False)
    monkeypatch.setattr(inst, "initial_affichage", lambda **k: False)
    monkeypatch.setattr(inst, "copy_examples", lambda: [])
    autre = tmp_path / "ailleurs"
    assert inst.main(["--sans-modeles", "--sans-demarrage", "--projets", str(autre)]) == 0
    assert autre.is_dir() and "(créé, vide" in capsys.readouterr().out
