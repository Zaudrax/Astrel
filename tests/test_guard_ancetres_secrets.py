"""Lecteurs récursifs sur un dossier ANCÊTRE d'un secret (22/10, plan P27, fiche 3 du relecteur,
accord de l'utilisateur ; docs/DURCISSEMENT-GARDE-FOU.md).

`grep -r x runtime`, `grep -rn token .` depuis la racine, `find runtime -name auth.json -exec cat
{} +` ne demandaient rien alors que l'outil Grep sur `runtime` est refusé : `runtime/cli/*/auth.json`
et `config/secrets.json` étaient lisibles. Désormais un lecteur récursif (grep -r, rg, ag, ugrep,
find -exec, find | xargs, `$(find …)`, cp -r, rsync, tar c, zip -r, diff -r) qui parcourt un
ancêtre d'un secret est une « lecture d'un secret » (action sur le garde-fou). Restent libres :
les sous-dossiers sans secret, les simples listes, les filtres qui écartent chaque secret à coup
sûr (`--include=*.py`, `--exclude-dir=runtime --exclude-dir=config`, `rg -t py`)."""
import time

import pytest

from astrel import config, guard, totalgate

R = config.ROOT
PARENT = R.parent  # dossier qui contient le projet


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "MODE_FILE", tmp_path / "autonomy.json")
    monkeypatch.setattr(guard, "GRANTS_FILE", tmp_path / "grants.json")


def _assess(cmd: str, cwd=R):
    return guard.assess("Bash", {"command": cmd}, mode="autonome", cwd=str(cwd))


@pytest.mark.parametrize("cmd, cwd", [
    # preuves du relecteur (fiche 3)
    ("grep -r x runtime", R),
    (f"grep -rn token {R}", "/tmp"),
    ("find runtime -name auth.json -exec cat {} +", R),
    # grep récursif sous toutes ses formes, sur la racine, runtime, config, ~, un parent
    ("grep -rn token .", R),
    ("grep -R token", R),
    ("grep --recursive token config", R),
    ("grep -d recurse token config", R),
    ("grep -r token *", R),
    ("cd astrel && grep -r token ..", R),
    ("grep -r token ~", "/tmp"),
    (f"grep -r token {PARENT}", "/tmp"),
    ("bash -c 'grep -r token runtime'", R),
    ("D=runtime; grep -r token $D", R),
    ("grep -r token $(echo runtime)", R),
    # chercheurs qui parcourent toujours
    ("rg token", R),
    ("rg token runtime", R),
    ("rg token", PARENT),
    ("ugrep -r token config", R),
    ("ag token config", R),
    # find -exec, find | xargs, $(find …)
    ("find runtime -name auth.json -print0 | xargs -0 cat", R),
    ("find . -type f -exec grep -l token {} +", R),
    ("cat $(find runtime -name auth.json)", R),
    ("grep -rn sig $(find . -name x)", R),
    # copies et archives : le secret part ailleurs, lisible
    ("cp -r runtime /tmp/x", R),
    ("cp -a . /tmp/sauve", R),
    ("tar czf /tmp/r.tgz config", R),
    ("tar czf /tmp/c.tgz --exclude=runtime .", R),
    ("rsync -a --exclude runtime ./ /tmp/c/", R),
    ("diff -r runtime /tmp/vide", R),
    # filtres qui n'écartent pas les secrets, ou pas à coup sûr
    ("grep -rn token --include=*.json .", R),
    ("grep -rn token --include={*.py,secrets.json} .", R),
    ("grep -rn token --exclude-dir=runtime .", R),
    ("rg -g '*.json' token", R),
    ("rg -g '!runtime' token", R),
    ("rg -t json token", R),
    ("rsync -a --include=config/secrets.json --exclude=config --exclude=runtime ./ /tmp/c/", R),
])
def test_lecteur_recursif_sur_un_ancetre_demande(cmd, cwd):
    v = _assess(cmd, cwd)
    assert v.needs_confirm and v.guard is True, (cmd, v.desc)
    assert guard.L_SECRET in v.desc, (cmd, v.desc)
    assert not v.keys and not v.lift  # ni « oui pour 30 min », ni levée
    assert not totalgate.total_grants(True, v.guard, False)  # ni Autonome TOTAL


@pytest.mark.parametrize("cmd, cwd", [
    ("grep -r foo astrel/", R),
    ("grep -rn foo tests", R),
    ("rg foo astrel tests", R),
    ("grep -r foo .", R / "astrel"),
    ("grep token runtime", R),                    # sans -r : un dossier n'est pas lu
    ("grep foo runtime/model.txt", R),
    ("ls -R runtime", R),
    ("find runtime -name '*.json'", R),
    ("find . -name '*.py'", R),
    ("find runtime -type f | xargs ls -l", R),
    ("find astrel -name '*.py' -exec grep -l foo {} +", R),
    ("du -sh runtime", R),
    ("tree runtime", R),
    ("rg --files", R),
    ("cp -r astrel /tmp/x", R),
    ("grep -r token ~/Documents", "/tmp"),
    # filtres qui écartent chaque secret
    ("grep -rn token --include=*.py .", R),
    ("grep -rn token --include=*.py --include=*.md .", R),
    ("grep -rn x --include=SKILL.md .", PARENT),
    ("grep -rn token --exclude-dir=runtime --exclude-dir=config .", R),
    ("grep -rn token --exclude-dir=config --include=*.py .", R),
    ("rg -t py token", R),
    ("rg -g '*.ts' token", R),
    ("rg -g '!runtime' -g '!config' token", R),
    ("rsync -a --exclude runtime --exclude config ./ /tmp/c/", R),
    ("tar czf /tmp/c.tgz --exclude=runtime --exclude=config .", R),
    # faux positifs vus au rejeu du corpus
    ("for p in a b; do grep -rln -- \"$p\" astrel --include='*.py'; done", R),
    ("grep -rn sig $(grep -rln X astrel tests)", R),
])
def test_sous_dossiers_listes_et_filtres_restent_libres(cmd, cwd):
    v = _assess(cmd, cwd)
    assert guard.L_SECRET not in (v.desc or ""), (cmd, v.desc)


def test_commande_d_un_mega_octet_en_temps_lineaire():
    # Fiche 2 du testeur : 1 Mo de commande prenait 25 s (`shlex`, quadratique sur un long mot).
    t = time.perf_counter()
    _assess("echo " + "a" * 1_000_000)
    assert time.perf_counter() - t < 5
