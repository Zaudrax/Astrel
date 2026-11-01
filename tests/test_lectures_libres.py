"""Lectures libres des fichiers protégés (15/10, docs/DURCISSEMENT-GARDE-FOU.md,
section « Lectures libres »).

Avant : une commande pas ENTIÈREMENT en lecture seule qui citait server.py, main.js…
n'importe où demandait « une modification de …, fichier protégé » (même en Total).
Vu dans logs/astrel.log : un `grep … server.py | head` suivi d'un `ss -ltnp`, un
`python -c "import playwright"` à côté d'un `grep … main.js`, un heredoc python qui
cite « astrel/server.py » dans une chaîne. Désormais chaque commande est jugée à part ;
les écritures réelles restent demandées (garde-fou, sans « oui pour 30 min »)."""
import chemins_machine  # chemins de la machine qui lance la suite
import pytest

from astrel import config, guard, totalgate

R = config.ROOT


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "MODE_FILE", tmp_path / "autonomy.json")
    monkeypatch.setattr(guard, "GRANTS_FILE", tmp_path / "grants.json")


def _assess(cmd: str, cwd=R):
    return guard.assess("Bash", {"command": cmd}, mode="autonome", cwd=str(cwd))


# Les trois commandes du journal (complètes, retrouvées dans la conversation).
CAS_A = (f"cd {R} && grep -n \"PORT\\|port\" astrel/server.py | head -20; grep -n \"HUD_PORT\" astrel/config.py; "
         "ss -ltnp 2>/dev/null | grep -E \"47[0-9]{2}\" ")
CAS_B = f""".venv/bin/python - <<'EOF'
from astrel import guard
import inspect
print(inspect.signature(guard.classify))
cmds=[r'grep -n "PORT" astrel/server.py', r'cd x && grep -n "x" astrel/server.py | head -5']
for c in cmds:
    try:
        r=guard.classify("Bash",{{"command":c}},cwd="{chemins_machine.RACINE}")
    except TypeError:
        r=guard.classify("Bash",{{"command":c}})
    print(r, "<-", c[:70])
EOF"""
CAS_C = (f"cd {R} && .venv/bin/python -c \"import playwright, sys; print('pw ok')\" 2>&1; "
         ".venv/bin/python -c \"import websockets; print('websockets ok')\" 2>&1; "
         "grep -n \"typingIn\\b\" hud/src/main.js | head -3; cat hud.sh 2>/dev/null | head -30")


@pytest.mark.parametrize("cmd, cwd", [
    (CAS_A, f"{chemins_machine.PROJETS}"),
    (CAS_B, R),
    (CAS_C, f"{chemins_machine.PROJETS}"),
    ("grep -n PORT astrel/server.py 2>/dev/null | sed -n 1,5p | awk '{print $1}'", R),
    ("grep -n x astrel/server.py | tee /tmp/sortie.txt", R),             # tee vers /tmp : pas vers le fichier
    ("cat hud/src/main.js 2>&1 | wc -l; ss -ltnp; pgrep -af astrel", R),
    ("grep -n x astrel/totalgate.py; ss -l", R),                          # totalgate.py : lu, pas modifié
    (".venv/bin/python -c \"print(open('astrel/state.py').read()[:200])\"", R),
    ("python3 -c \"print(open('astrel/state.py', 'rb').read()[:9], open('hud/index.html', mode='r').read(9))\"", R),
    ("node -e \"console.log(require('fs').readFileSync('hud/src/main.js','utf8').length)\"", R),
    ("python3 - astrel/server.py <<'EOF'\nimport sys\nprint(open(sys.argv[1]).read().count('def '))\nEOF", R),
    ("git diff astrel/server.py | head; git log -3 --oneline -- hud/src; git show HEAD:astrel/state.py | head", R),
    ("diff dist/app.js /tmp/app.js; npm run build 2>&1 | tail -2", R / "hud"),
    ("cat >> docs/NOTES.md <<'EOF'\nvoir astrel/server.py et hud/src/main.js\nEOF", R),  # texte qui CITE le nom
    ("cd hud/src && python3 -c \"print(len(open('main.js').read()))\"", R),  # dans un dossier protégé, lecture
    ("awk 'NR>=10 && NR<=20' astrel/server.py", R),
    (".venv/bin/python -m pyflakes astrel/server.py astrel/state.py", R),
    ("ls -la hud/src hud/dist; stat hud/index.html; wc -l astrel/bubble.py astrel/minichat.py", R),
    ("find hud/src -name '*.js' -exec grep -l TOTAL {} +", R),
    ("cd /tmp/autre && node build.mjs server.py index.html", "/tmp"),    # homonymes d'un autre projet
])
def test_lecture_de_fichier_protege_libre(cmd, cwd):
    v = _assess(cmd, cwd)
    assert not v.needs_confirm, (cmd, v.desc)


@pytest.mark.parametrize("cmd, cwd", [
    ("grep -n x astrel/server.py; sed -i 's/a/b/' astrel/server.py", R),
    ("grep x astrel/server.py > /dev/null; echo y >> astrel/server.py", R),
    ("cat /tmp/x | tee hud/src/main.js > /dev/null", R),
    ("cp /tmp/x.py astrel/state.py 2>&1", R),
    ("mv /tmp/x.html hud/index.html", R),
    ("install -m644 /tmp/b.py astrel/bubble.py", R),
    ("ln -s /tmp/x astrel/minichat.py", R),
    (f"ln -s {R}/astrel/server.py /tmp/lien", R),                         # écrire par le lien, plus tard
    ("cd astrel && chmod 000 server.py", R),
    ("cd hud/src && npx prettier --write .", R),
    ("python3 -c \"open('astrel/server.py','w').write('x')\"", R),
    ("python3 -c \"m = 'w'; open('astrel/server.py', m).write('')\"", R),  # mode calculé : dans le doute
    ("python3 - <<'EOF'\nfrom pathlib import Path\np = Path('astrel/server.py')\n"
     "p.write_text(p.read_text().replace('a', 'b'))\nEOF", R),
    ("cd hud/src && python3 - <<'EOF'\np = 'main.js'\ns = open(p).read()\nopen(p, 'w').write(s)\nEOF", R),
    ("python3 -c \"import subprocess; subprocess.run(['sed', '-i', 's/a/b/', 'astrel/state.py'])\"", R),
    ("python3 -c \"import os; os.replace('/tmp/x', 'astrel/minichat.py')\"", R),
    ("echo \"open('astrel/server.py', 'a').write('#')\" | python3", R),
    ("node -e \"require('fs').writeFileSync('hud/src/main.js', '')\"", R),
    ("perl -e 'open(F, \">astrel/state.py\")'", R),
    ("python3 tools/outil.py astrel/server.py", R),                      # script sur le disque : inconnu
    ("cat /tmp/p.py | python3 - astrel/server.py", R),                   # code venu d'un tuyau : inconnu
    ("patch -p1 <<'EOF'\n--- a/astrel/server.py\n+++ b/astrel/server.py\nEOF", R),
    ("gawk -i inplace '{print}' astrel/server.py", R),
    ("awk '{print > \"hud/index.html\"}' /tmp/x", R),
    ("bash -c 'sed -i s/a/b/ \"$1\"' _ astrel/server.py", R),
    ("$EDITOR astrel/server.py", R),
    ("sed -i 's/False/True/' totalgate.py", "/tmp"),                     # par son nom, d'où qu'il soit
    ("grep x astrel/server.py; echo \"non fermé > astrel/server.py", R),  # illisible : repli sur le texte
])
def test_ecriture_de_fichier_protege_demandee(cmd, cwd):
    v = _assess(cmd, cwd)
    assert v.needs_confirm and v.guard and not v.keys, (cmd, v.desc)
    assert not totalgate.total_grants(True, v.guard, False)  # même en Total


def test_code_ecrit_ailleurs_mais_citant_le_fichier_demande():
    """Règle demandée : un script qui cite un fichier protégé ET contient une API
    d'écriture demande, même si l'écriture vise un autre fichier (analyse de texte)."""
    cmd = "python3 -c \"s = open('astrel/server.py').read(); open('/tmp/copie.py', 'w').write(s)\""
    assert _assess(cmd).needs_confirm
    assert not _assess("python3 -c \"s = open('astrel/core.py').read(); open('/tmp/c.py', 'w').write(s)\"").needs_confirm


def test_les_noms_du_garde_fou_restent_au_texte():
    """15/10 : guard.py, confirm.py, hooks… restaient jugés sur le texte entier. Depuis le
    19/10 (« assouplis la règle », l'utilisateur) : seulement s'ils sont cités par une commande
    qui n'est pas un lecteur sûr (voir « Noms du garde-fou cités pour être LUS » plus bas)."""
    assert not _assess("grep -n x astrel/guard.py; ss -l").needs_confirm
    v = _assess("grep -n x astrel/guard.py; ss -l; python3 astrel/guard.py")
    assert v.needs_confirm and v.guard


# --- Variables lanceuses (15/10, section « Variables lanceuses » de la doc) ---
# Une variable qui fait lancer ou charger un autre programme (pager, éditeur,
# préprocesseur, chemin de recherche, bibliothèque, configuration lue ailleurs) rend
# le « lecteur » qui suit non sûr : un fichier protégé cité demande. Les valeurs sont
# volontairement neutres (/tmp/outil) : seule la présence de la variable compte.

@pytest.mark.parametrize("cmd, cwd", [
    # posée devant la commande
    ("LESSOPEN='|/tmp/outil %s' less astrel/server.py", R),
    ("LESSCLOSE='/tmp/outil %s %s' less hud/index.html", R),
    ("PAGER=/tmp/outil git log -p -- astrel/state.py", R),
    ("GIT_PAGER=/tmp/outil git show HEAD:astrel/server.py", R),
    ("GIT_EXTERNAL_DIFF=/tmp/outil git diff astrel/bubble.py", R),
    ("GIT_SSH_COMMAND=/tmp/outil git log hud/src/main.js", R),
    ("EDITOR=/tmp/outil VISUAL=/tmp/outil git diff astrel/minichat.py", R),
    ("GIT_EDITOR=/tmp/outil git status hud/dist", R),
    ("MANPAGER=/tmp/outil man ./hud/index.html", R),
    ("BROWSER=/tmp/outil cat astrel/server.py", R),
    ("PROMPT_COMMAND=/tmp/outil grep -n x astrel/server.py", R),
    ("LD_PRELOAD=/tmp/b.so cat astrel/server.py", R),
    ("PYTHONSTARTUP=/tmp/outil.py head astrel/state.py", R),
    ("PATH=/tmp/b:$PATH wc -l astrel/server.py", R),
    ("A=1 PAGER=/tmp/outil B=2 git log astrel/server.py | head", R),
    # par un préfixe qui lance le programme
    ("env GIT_PAGER=/tmp/outil git show HEAD:astrel/server.py", R),
    ("env -u X LESSOPEN='|/tmp/outil %s' less astrel/server.py", R),
    ("nohup env PAGER=/tmp/outil git log astrel/state.py", R),
    # exportée ou affectée plus tôt dans la même ligne
    ("export LESSOPEN='|/tmp/outil %s'; less astrel/server.py", R),
    ("export PAGER=/tmp/outil && git log -p astrel/server.py", R),
    ("LESSOPEN='|/tmp/outil %s'; less astrel/server.py", R),
    ("declare -x GIT_PAGER=/tmp/outil; git diff astrel/state.py | cat", R),
    ("export PATH=/tmp/b:$PATH; cd astrel && grep -n x server.py", R),
    ("export PAGER=/tmp/outil; (cd astrel && git diff state.py)", R),
    ("cd hud && export LESSOPEN='|/tmp/outil %s' && less src/main.js", R),
    # la VALEUR elle-même écrit le fichier protégé
    ("export PAGER='cp /tmp/x astrel/server.py'; git log", R),
    ("env PAGER='cp /tmp/x astrel/server.py' git log", R),
])
def test_variable_lanceuse_devant_un_lecteur_demande(cmd, cwd):
    v = _assess(cmd, cwd)
    assert v.needs_confirm and v.guard, (cmd, v.desc)


@pytest.mark.parametrize("cmd, cwd", [
    ("less astrel/server.py", R),
    ("git log -3 --oneline -- astrel/server.py; git diff hud/src/main.js | head", R),
    ("git show HEAD:astrel/server.py | head -20", R),
    ("LANG=C grep -n x astrel/server.py", R),                        # variable sans effet de lancement
    ("export LC_ALL=C; grep -n x astrel/state.py | sort", R),
    ("FOO=1 git diff astrel/server.py", R),
    ("PAGER=/tmp/outil git log astrel/core.py", R),                  # aucun fichier protégé cité
    ("export PAGER=cat; git log --oneline | head", R),
    ("MANPAGER='less -R' man ls", R),
    ("export PAGER=/tmp/outil; unset PAGER; less astrel/server.py", R),
    ("PAGER=/tmp/outil true; less astrel/server.py", R),             # ne valait que pour « true »
    ("(export PAGER=/tmp/outil); git diff astrel/server.py", R),     # sous-shell : ne déborde pas
    ("export PAGER=/tmp/outil; echo voir astrel/server.py", R),      # commande interne du shell
])
def test_lecture_sans_variable_lanceuse_reste_libre(cmd, cwd):
    v = _assess(cmd, cwd)
    assert not v.needs_confirm, (cmd, v.desc)


# Relecture du 15/10 : l'environnement peut changer par une voie que l'analyse ne
# suit pas (code relu, fonction de la ligne, script lu, réglage de git qui désigne un
# programme, variable au nom calculé) : il est alors tenu pour inconnu, une variable
# lanceuse est supposée posée. Et une VALEUR de variable qui cite un fichier protégé
# demande (des programmes écrivent dans le fichier nommé par une variable).
@pytest.mark.parametrize("cmd", [
    "printf -v PAGER %s /tmp/outil; export PAGER; git log astrel/server.py",
    "eval 'export PAGER=/tmp/outil'; git log astrel/server.py",
    "f() { export LESSOPEN='|/tmp/outil %s'; }; f; less astrel/server.py",
    "source /tmp/env.sh; less astrel/server.py",
    "git config core.pager /tmp/outil && git log -p astrel/state.py",
    "V=PAGER; export \"$V=/tmp/outil\"; git log astrel/server.py",
    f"GIT_TRACE={R}/astrel/server.py git status",
    "GIT_TRACE2_EVENT=astrel/server.py git status",
    "env GIT_TRACE=astrel/server.py git status",
    "export GIT_TRACE=astrel/server.py; git status",
    "T=hud/src/main.js; export T; git status",
    "LESSHISTFILE=hud/index.html less README.md",
])
def test_environnement_inconnu_ou_valeur_citant_un_fichier_protege_demande(cmd):
    v = _assess(cmd)
    assert v.needs_confirm and v.guard, (cmd, v.desc)


@pytest.mark.parametrize("cmd", [
    "printf -v X %s 1; git log astrel/core.py",                      # aucun fichier protégé cité
    "eval 'X=1'; grep -n x astrel/core.py",
    "f() { echo x; }; f; git log --oneline | head",
    "git config --get user.name; git log -3 -- astrel/server.py",   # lecture d'un réglage sans programme
    "GIT_TRACE=1 git status",
    "F=astrel/server.py; grep -n x \"$F\"",                         # variable non exportée, simple lecture
    "printf '%s\\n' astrel/server.py | head",
])
def test_environnement_suivi_et_lecture_restent_libres(cmd):
    v = _assess(cmd)
    assert not v.needs_confirm, (cmd, v.desc)


# --- Noms du garde-fou cités pour être LUS (19/10, « assouplis la règle », l'utilisateur) ---
# Avant : guard.py, confirm.py, hooks/, ESTOP… cités n'importe où dans une commande pas
# ENTIÈREMENT en lecture seule = « une action sur le garde-fou d'Astrel », même levé.
# Vu le 19/10 à 09 h 14 (CAS_REEL). Désormais une commande dont les seules citations sont
# faites par des LECTEURS SÛRS (ls, cat, grep, sha256sum, sed sans -i, git diff…), sans
# substitution, redirection d'écriture, tuyau vers autre chose qu'un lecteur sûr, ni
# changement d'état du shell, est jugée comme si elle ne les citait pas.

CAS_REEL = ("ls -l --time-style=long-iso hooks/empreinte.json; ls -lt --time-style=long-iso astrel/ipc.py "
            "astrel/platform/proc.py astrel/state.py astrel/confirm.py 2>&1; timeout 60 ./run.sh --doctor 2>&1 "
            "| tail -25; ps aux | grep -c \"[a]strel.*core\\|[r]un.sh\"")


@pytest.mark.parametrize("cmd", [
    CAS_REEL,
    "ls -l hooks/empreinte.json; ls -lt astrel/confirm.py astrel/state.py && ./run.sh --doctor",
    "sha256sum astrel/guard.py astrel/confirm.py hooks/permission_hook.py; ./run.sh --doctor 2>&1 | tail -5",
    "sed -n 3300,3340p astrel/guard.py; pgrep -af astrel",
    "git diff -- astrel/guard.py | head -40; git status --short; ps aux | grep -c '[a]strel'",
    "# empreinte du garde-fou\nsha256sum astrel/guard.py\n./run.sh --doctor",
    "wc -l astrel/guard.py astrel/confirm.py && timeout 60 ./run.sh --doctor",
    "stat -c %y hooks/permission_hook.py; file astrel/confirm.py; ps aux | grep -c '[r]un.sh'",
    "diff astrel/guard.py /tmp/guard.bak | head; ./run.sh --doctor",
    "tail -3 logs/audit.jsonl; cat runtime/ESTOP 2>/dev/null; ss -ltnp | grep 47",
    "grep -n '_cited_guard' astrel/guard.py | head -5; .venv/bin/python tools/usage_report.py --days 7",
])
def test_lecteur_qui_cite_le_garde_fou_ne_demande_plus(cmd):
    v = _assess(cmd)
    assert not v.needs_confirm and not v.guard, (cmd, v.desc)
    assert not guard.classify("Bash", {"command": cmd}, cwd=str(R)).guard


@pytest.mark.parametrize("cmd", [
    # le fichier est lancé, sourcé ou réécrit dans la même ligne
    "ls astrel/guard.py; python3 astrel/guard.py",
    "timeout 5 python3 astrel/guard.py; ls astrel/guard.py",
    "ls astrel/guard.py; echo $(python3 astrel/guard.py)",
    "ls astrel/guard.py; echo `bash hooks/permission_hook.py`",
    "ls astrel/guard.py; echo $(cat /tmp/x)",                            # substitution : jamais sûre
    "cat astrel/guard.py | sh",
    "cat astrel/confirm.py | python3 -",
    "cat astrel/guard.py |& python3",
    "cat /tmp/x > astrel/guard.py",
    "ls astrel/guard.py; cat /tmp/x > astrel/guard.py",
    "cat astrel/guard.py > /tmp/g.py; python3 /tmp/g.py",
    "ls astrel/guard.py; sed -i 's/a/b/' astrel/guard.py",
    "ls astrel/guard.py; cat /tmp/x | tee astrel/guard.py",
    "grep -r x . --include=guard.py -l | xargs rm",
    "awk 'BEGIN{system(\"python3 astrel/guard.py\")}'",
    "ls astrel; find astrel -name guard.py -exec python3 {} \\;",
    "git checkout -- astrel/guard.py",
    "git apply --include=astrel/guard.py /tmp/p.diff",
    "ls astrel/guard.py; git apply /tmp/p.diff",                         # patch opaque à côté
    "ls astrel/guard.py; patch -p1 < /tmp/p.diff",
    "ls astrel/guard.py; rm astrel/guard.py",
    "ls astrel/guard.py; chmod 777 astrel/guard.py",
    "ls astrel/guard.py; cp /tmp/x astrel/guard.py",
    "ls astrel/confirm.py; mv /tmp/x astrel/confirm.py",
    "cat runtime/ESTOP; rm runtime/ESTOP",
    "ls astrel/guard.py; ./run.sh --send autonomy prudent",
    "echo '{\"cmd\": \"estop off\"}' | nc -U runtime/control.sock",
    # variables, boucles, fonctions : le nom passe à une autre commande
    "G=astrel/guard.py; ls $G; python3 $G",
    "ls astrel/guard.py; python3 $_",
    "ls astrel/guard.py; ss -l; .venv/bin/python -m pytest $_",
    "for f in astrel/guard.py; do python3 $f; done",
    "ls astrel/guard.py | while read f; do python3 \"$f\"; done",
    "printf -v X %s astrel/guard.py; set -a; python3 /tmp/x.py",
    "ls(){ python3 \"$@\"; }; ls astrel/guard.py",
    # l'état du shell change : le « lecteur » n'est plus sûr
    "export PATH=/tmp/b:$PATH; ls astrel/guard.py; ss -l",
    "hash -p /tmp/outil ls; ls astrel/guard.py; ss -l",
    ". /tmp/env.sh; cat astrel/guard.py; ss -l",
    "PAGER=/tmp/outil git log astrel/guard.py; ss -l",
    # faux lecteurs
    "./cat astrel/guard.py",
    "./cat astrel/guard.py; ./run.sh --doctor",
    "less astrel/guard.py; ss -l",
    # commentaires : ce que le shell exécute vraiment
    "ls astrel/guard.py x#y; python3 astrel/guard.py",
    "# note\npython3 astrel/confirm.py",
    "ls # voir\nbash hooks/permission_hook.py",
])
def test_le_garde_fou_lance_ecrit_ou_passe_plus_loin_demande_toujours(cmd):
    v = _assess(cmd)
    assert v.needs_confirm and v.guard and not v.keys, (cmd, v.desc)
    assert not totalgate.total_grants(True, v.guard, False)  # même en Total


@pytest.mark.parametrize("cmd", [
    "ls astrel/guard.py; sudo ls /root",
    "cat astrel/confirm.py; git push",
    "ls hooks/empreinte.json; rm -rf ~/Documents/x",
    "sha256sum astrel/guard.py; echo x >> logs/audit.jsonl",
    "ls astrel/guard.py; ./run.sh --send estop",
])
def test_actions_dangereuses_a_cote_restent_confirmees(cmd):
    v = _assess(cmd)
    assert v.needs_confirm and v.level == guard.CRITICAL, (cmd, v.desc)


@pytest.mark.parametrize("cmd, segments", [
    ("ls x#y; python3 a.py", [["ls", "x#y"], ["python3", "a.py"]]),
    ("echo $#; echo ${#x} '#' \"#\"", [["echo", "$#"], ["echo", "${#x}", "#", "#"]]),
    ("ls a\\ #b; python3 c.py", [["ls", "a #b"], ["python3", "c.py"]]),
    ("ls # rien > ici\ncat f", [["ls"], ["cat", "f"]]),
])
def test_commentaires_lus_comme_le_shell(cmd, segments):
    assert guard._segments(guard._plain(cmd)) == segments


# --- 19/10, relecture : deux trous (preuve du relecteur) ---
# 1) À côté d'une lecture du garde-fou, une sous-commande git qui réécrit des fichiers
#    suivis SANS les nommer passait (`_GIT_OPAQUE` incomplet) : liste BLANCHE désormais.
# 2) sed : la commande `e` exécute, `w`/`W` écrivent, `r`/`R` lisent un autre fichier,
#    le drapeau `e`/`w` de s/// aussi ; `$!w …` et `s|a|b|e` échappaient à l'expression.
#    Idem `--i` (abréviation de --in-place), `less -o`, `bat --pager`, `awk -v f=…`.

G = "astrel/guard.py"


@pytest.mark.parametrize("cmd", [
    f"git log {G} && git checkout-index -a -f",
    f"git log {G} && git read-tree -u HEAD",
    f"git log {G}; git sparse-checkout set docs",
    f"git log {G}; git bisect start",
    f"git log {G}; git update-ref -d HEAD",
    f"git log {G}; git rm --cached x",
    f"git log {G}; git mv a b",
    f"git log {G}; git difftool",
    f"git log {G}; git mergetool",
    f"git log {G}; git -C . checkout-index -a -f",
    f"git log {G}; git -c alias.l=log l",
    f"git log {G}; git co",                                             # alias inconnu
    f"git log {G}; git branch -f main HEAD~3",
    f"git log {G}; git branch nouvelle",
    f"git log {G}; env git checkout-index -a -f",                       # derrière un préfixe
    f"git log {G}; nice git checkout-index -a -f",
    f"git log {G}; timeout 5 git checkout-index -a -f",
    f"git log {G}; bash -c 'git checkout-index -a -f'",
    f"git log {G}; env patch -p1 < /tmp/p.diff",
    f"echo 'cp /tmp/x astrel/ipc.py' | sed e; ls {G}",
    f"sed -n '$!w {G}'",
    f"sed -n '$!W {G}' /tmp/x",
    f"sed 's|a|b|w {G}' /tmp/x",
    f"sed 's/a/b/e' {G}",
    f"sed 's|a|b|e' {G}",
    f"sed -n '1,3e' {G}",
    f"sed 'r /tmp/x' {G}",
    f"sed '1R /tmp/x' {G}",
    f"sed '/a/!w /tmp/y' {G}",
    f"sed -e p -e 'w /tmp/y' {G}",
    f"sed --expression='w /tmp/y' {G}",
    f"sed --i 's/a/b/' {G}",                                            # abréviation de --in-place
    f"cat {G} | sed e",
])
def test_trous_git_et_sed_a_cote_du_garde_fou_demandent(cmd):
    v = _assess(cmd)
    assert v.needs_confirm and not v.keys, (cmd, v.desc)


@pytest.mark.parametrize("cmd", [
    "echo 'cp /tmp/x astrel/ipc.py' | sed e",
    "sed e /tmp/cmds",
    "sed 's|a|b|e' /tmp/x",
    "sed -n '$!w astrel/ipc.py' /tmp/x",
    "sed '0~2w astrel/ipc.py' /tmp/x",
    "sed --i 's/a/b/' astrel/ipc.py",
    "sed --in-pl 's/a/b/' astrel/ipc.py",
    "less -o astrel/ipc.py /tmp/x",
    "less --log-file=astrel/ipc.py /tmp/x",
    "less -O astrel/ipc.py /tmp/x",
    "bat --paging=always --pager 'cp /tmp/x astrel/ipc.py' astrel/ipc.py",
    "awk -v f=astrel/ipc.py 'BEGIN{print 1 > f}'",
])
def test_lecteurs_qui_ecrivent_ou_lancent_demandent(cmd):
    v = _assess(cmd)
    assert v.needs_confirm and not v.keys, (cmd, v.desc)


@pytest.mark.parametrize("cmd", [
    f"sed -n p {G}",
    f"sed -n '1,5p' {G}",
    f"sed 's/a/b/g' {G} | head",
    f"sed -n '/def /p' {G}",
    f"sed -n '/write/p;/read/p' {G}",                                   # w, e, r DANS un motif
    f"sed 's/we/WE/g; s#/w#x#' {G}",
    f"sed -n -e '/^def /{{p;q}}' -e '$=' {G}",
    f"sed -E 's/(a|b)+/X/2' {G}",
    f"sed -n '3300,3340p;3400q' {G}",
    f"sed '1i\\\\entete' {G} | head -2",
    f"sed -n '0,/re/p' {G}",
    f"sed 'y/abc/xyz/' {G} | wc -l",
    f"git status; git log {G}; git branch; git branch -a; git cat-file -p HEAD | head",
    f"git -C . log -1 --oneline -- {G}; git rev-parse HEAD; git branch --show-current",
    f"git diff --stat; sha256sum {G}; git ls-files | wc -l",
    f"git branch --list 'feat*'; ls {G}",
])
def test_lectures_simples_git_et_sed_restent_libres(cmd):
    v = _assess(cmd)
    assert not v.needs_confirm and not v.guard, (cmd, v.desc)


@pytest.mark.parametrize("cmd", [
    "less -R hud/src/main.js",
    "more astrel/ipc.py",
    "bat -n astrel/ipc.py",
    "awk 'NR<5' astrel/ipc.py",
    "sed -n '/w /p' astrel/ipc.py",
    "perl -ne 'print if /def /' astrel/ipc.py",
])
def test_lecteurs_simples_de_fichiers_proteges_restent_libres(cmd):
    v = _assess(cmd)
    assert not v.needs_confirm, (cmd, v.desc)
