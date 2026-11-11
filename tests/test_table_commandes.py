"""T58 pas 1 (04/11) : `_on_command` en table nom → méthode. Ce test FIGE, avant et après le déplacement du
code, les noms traités (147 avec history_query, 04/11 ; 149 avec local_vers_claude et local_demandes, 05/11 ;
152 avec les propositions du modèle local, lot 5, 05/11 ; 153 avec chat_commande, commandes « / » du chat, 05/11 ;
155 avec pentest et teste_cible, capacité de pentest, 09/11) et les refus faits à Claude (message exact)."""
from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

from astrel import core

NOMS = {
    "agent_effort", "announce", "ask_choice", "attach_stage", "attachments_stage", "audit_get", "autonomy",
    "autorisations", "calibrage_cancel", "calibrage_save", "calibrage_start", "canal", "canaux", "capacites_get",
    "channel", "channel_create", "channel_delete", "channel_edit", "channel_move", "channel_rename",
    "channel_reorder", "channels", "chat_commande", "choice_answer", "chut", "confirm", "couper_tout", "dictation", "dictee",
    "end_listen", "endlisten", "estop", "followup", "grants", "graph", "guide_get", "guide_mark", "history_query", "hud_here",
    "hud_pages", "hud_replace", "hush", "image_compte_ajouter", "image_compte_connecter", "image_compte_ordre",
    "image_compte_renommer", "image_compte_retirer", "image_generate", "image_piece", "image_retry", "image_status",
    "initiative", "interrupt", "lire", "local", "local_demandes", "local_off", "local_on", "local_vers_claude", "media_open", "media_playing", "open_link",
    "memory_top", "mic", "micprofile", "mics", "mission", "mission_auto", "mission_close", "mode", "model",
    "models", "mute", "mute_all", "mute_voice", "off", "on", "pause", "photo", "plan_comment", "plan_get",
    "plan_list", "plan_reject", "plan_reply", "plan_revise", "plan_submit", "plan_validate", "plans_get",
    "proposition_appliquer", "proposition_refuser", "propositions_get", "quit", "quota_refresh", "rapports",
    "proposition_local_appliquer", "proposition_local_refuser", "propositions_local_get",  # 05/11 (lot 5)
    "read_report", "reflex_allowed", "reflex_create", "reflex_delete", "reflex_enable", "reflexes", "report",
    "reports", "reprendre", "reprends", "reset", "restart", "resume", "say", "sensor", "sensors",
    "settings_action", "settings_apply", "settings_get", "settings_set", "show_media", "silence", "status", "stop",
    "stop-listening", "stop-tout", "stop_task", "stop_tout", "stoptout", "suite", "tais-toi", "task_detail",
    "pentest", "teste_cible",  # 09/11 : capacité de pentest (cyber-local), l'utilisateur seul
    "outbox_stage", "outbox_get", "outbox_accept", "outbox_refuse", "outbox_edit", "outbox_revise",  # 09/11 (T88)
    "task_stop", "todo_add", "todo_done", "todo_drop", "todo_edit", "todo_get", "todo_list", "todo_move",
    "todo_ordre", "todos_get", "toggle", "total", "unmute", "usage", "video_cancel", "video_generate",
    "video_status", "voice", "voice_mute", "voices", "wake", "whatsapp", "whatsapp_file", "whatsapp_file_status",
}

# Réponse exacte du cœur à une commande envoyée par Claude (relevée sur le code du 04/11 avant la table).
REFUS = {
    "task_detail": "hud_only", "memory_top": "hud_only", "agent_effort": "hud_only",
    "mic": "choix et réglage du micro réservés à l'utilisateur (HUD, terminal), refusés à Claude",
    "micprofile": "choix et réglage du micro réservés à l'utilisateur (HUD, terminal), refusés à Claude",
    "calibrage_start": "calibrage du micro réservé à l'utilisateur (HUD), refusé à Claude",
    "calibrage_save": "calibrage du micro réservé à l'utilisateur (HUD), refusé à Claude",
    "settings_set": "réglages réservés à l'utilisateur (HUD ou terminal), refusés à Claude",
    "settings_apply": "réglages réservés à l'utilisateur (HUD ou terminal), refusés à Claude",
    "plan_validate": "plans réservés à l'utilisateur (HUD ou terminal), refusés à Claude",
    "plan_reject": "plans réservés à l'utilisateur (HUD ou terminal), refusés à Claude",
    "proposition_appliquer": "propositions de mémoire réservées à l'utilisateur (HUD ou terminal), refusées à Claude",
    "proposition_refuser": "propositions de mémoire réservées à l'utilisateur (HUD ou terminal), refusées à Claude",
    # 05/11 (lot 5 du moteur local) : propositions de modification du modèle local
    "propositions_local_get": "propositions du modèle local réservées à l'utilisateur (HUD), refusées à Claude",
    "proposition_local_appliquer": "propositions du modèle local réservées à l'utilisateur (HUD), refusées à Claude",
    "proposition_local_refuser": "propositions du modèle local réservées à l'utilisateur (HUD), refusées à Claude",
    "propositions_get": "propositions de mémoire réservées à l'utilisateur (HUD ou terminal), refusées à Claude",
    "confirm": "commande réservée à l'utilisateur (HUD ou terminal), refusée à Claude",
    "mission_close": "commande réservée à l'utilisateur (HUD ou terminal), refusée à Claude",
    "voice_mute": "coupures du micro et de la voix : l'utilisateur seul (HUD, bulle, terminal)",
    "mute_all": "coupures du micro et de la voix : l'utilisateur seul (HUD, bulle, terminal)",
    "choice_answer": "réponse réservée à l'utilisateur (HUD), refusée à Claude",
    "audit_get": "journal d'audit réservé à l'utilisateur (HUD ou terminal), refusé à Claude",
    "guide_mark": "guide réservé à la personne devant l'écran, refusé à Claude",
    "capacites_get": "page des capacités réservée à la personne devant l'écran, refusée à Claude",
    "history_query": "history_query est réservé au HUD de l'utilisateur",  # 04/11 : recherche du HUD
    "local_vers_claude": "local_vers_claude est réservé au HUD de l'utilisateur",  # 05/11 (lot 6 du moteur local)
    "local_demandes": "local_demandes est réservé au HUD de l'utilisateur",
    "chat_commande": "chat_commande est réservé au HUD de l'utilisateur",  # 05/11 : commandes « / » du chat
    # 09/11 (cyber-local) : le cerveau ne lance JAMAIS de pentest
    "pentest": "pentest réservé à l'utilisateur (voix ou HUD), refusé à Claude",
    "teste_cible": "pentest réservé à l'utilisateur (voix ou HUD), refusé à Claude",
    # 09/11 (T88) : file des envois en attente (outbox_stage, lui, est permis à Claude : il PROPOSE)
    "outbox_get": "file d'envoi réservée à l'utilisateur (HUD), refusée à Claude",
    "outbox_accept": "file d'envoi réservée à l'utilisateur (HUD), refusée à Claude",
    "outbox_refuse": "file d'envoi réservée à l'utilisateur (HUD), refusée à Claude",
    "outbox_edit": "file d'envoi réservée à l'utilisateur (HUD), refusée à Claude",
    "outbox_revise": "file d'envoi réservée à l'utilisateur (HUD), refusée à Claude",
}


def _noms_dans_le_code() -> set[str]:
    """Noms testés en ligne dans `_on_command` (prologue, ou tout avant la table) + clés de la table."""
    fn = ast.parse(textwrap.dedent(inspect.getsource(core.Astrel._on_command))).body[0]
    out: set[str] = set()

    def valeurs(n):
        if isinstance(n, ast.Constant):
            return [n.value]
        if isinstance(n, (ast.Tuple, ast.List, ast.Set)):
            r = []
            for e in n.elts:
                r += list(eval(compile(ast.Expression(e.value), "t", "eval"), vars(core))) \
                    if isinstance(e, ast.Starred) else valeurs(e)
            return r
        return list(eval(compile(ast.Expression(n), "t", "eval"), vars(core)))

    for st in fn.body:
        if isinstance(st, ast.If):
            t = st.test.values[0] if isinstance(st.test, ast.BoolOp) else st.test
            if isinstance(t, ast.Compare) and isinstance(t.left, ast.Name) and t.left.id == "name":
                out.update([t.comparators[0].value] if isinstance(t.ops[0], ast.Eq) else valeurs(t.comparators[0]))
    return out | set(getattr(core.Astrel, "_COMMANDES", {}))


def test_tous_les_noms_sont_traites():
    assert _noms_dans_le_code() == NOMS


def test_commande_inconnue():
    j = object.__new__(core.Astrel)
    j._off = False
    assert j._on_command({"cmd": "n_existe_pas"}) == {"ok": False, "error": "commande inconnue : n_existe_pas"}


@pytest.mark.parametrize("nom", sorted(REFUS))
def test_refus_a_claude_inchanges(nom):
    j = object.__new__(core.Astrel)
    j._off = False
    r = j._on_command({"cmd": nom, "_from_claude": True, "_via": "socket"})
    assert r == {"ok": False, "error": REFUS[nom]}


def test_refus_note_au_journal_d_audit(monkeypatch):
    vus = []
    monkeypatch.setattr(core.audit, "record", lambda *a, **k: vus.append(a))
    j = object.__new__(core.Astrel)
    j._off = False
    j.on_command({"cmd": "settings_set", "_from_claude": True, "_via": "socket"})
    assert vus and vus[0][:3] == ("refus", "claude", "settings_set")


def test_table_sans_doublon_et_methodes_presentes():
    table = getattr(core.Astrel, "_COMMANDES", None)
    if table is None:
        pytest.skip("table pas encore en place")
    for nom, meth in table.items():
        assert callable(getattr(core.Astrel, meth, None)), (nom, meth)
