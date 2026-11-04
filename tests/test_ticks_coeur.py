"""T58 pas 4 (04/11) : un try par tick de la boucle du cœur, journal borné pour un tick en panne."""
from __future__ import annotations

import logging

from astrel import core
from astrel.coeur import mission as coeur_mission


def _faux(ordre: list[str], panne: set[str]):
    j = object.__new__(core.Astrel)
    for _libelle, meth in coeur_mission.MissionCoeur._TICKS:
        def tick(meth=meth):
            ordre.append(meth)
            if meth in panne:
                raise RuntimeError(meth)
        setattr(j, meth, tick)

    class Arret:
        def tick(self):
            ordre.append("_quota_arret")
    j._quota_arret_obj = Arret()
    return j


def test_un_tick_qui_leve_n_empeche_pas_les_suivants_dans_l_ordre():
    ordre: list[str] = []
    j = _faux(ordre, {"_estop_watch", "_tasks_tick"})
    j._ticks_une_fois()
    attendu = [m for _l, m in coeur_mission.MissionCoeur._TICKS] + ["_quota_arret"]
    assert ordre == attendu
    assert ordre[0] == "_estop_watch" and "_mission_tick" in ordre  # la mission tourne même si l'ESTOP a planté


def test_tous_les_ticks_existent_sur_le_coeur():
    for _libelle, meth in coeur_mission.MissionCoeur._TICKS:
        assert callable(getattr(core.Astrel, meth, None)), meth


def test_journal_d_un_tick_en_panne_borne(monkeypatch, caplog):
    ordre: list[str] = []
    j = _faux(ordre, {"_courrier_tick"})
    horloge = [1000.0]
    monkeypatch.setattr(coeur_mission.time, "monotonic", lambda: horloge[0])
    with caplog.at_level(logging.WARNING, logger=core.log.name):
        j._ticks_une_fois()
        horloge[0] += 10
        j._ticks_une_fois()
        j._ticks_une_fois()
        horloge[0] += 61
        j._ticks_une_fois()
    lignes = [r for r in caplog.records if "courrier" in r.getMessage()]
    assert len(lignes) == 2
    assert lignes[0].exc_info and not lignes[1].exc_info  # trace pleine d'abord, puis une ligne par minute


# --- T58 pas 5 (04/11) : le repos sorti de `run`, aussi en mode texte


def _repos(monkeypatch, conversation=False, leger=False, dernier_tour=0.0):
    j = object.__new__(core.Astrel)
    vus = []

    class Releve:
        def tick(self):
            vus.append("releve")
    j.relief = Releve()
    j._in_conversation = conversation
    j._local_active = lambda: leger
    j._last_turn = dernier_tour
    j._tasks = {}
    j._turn_active = False
    j.reset_session = lambda: vus.append("reset")
    monkeypatch.setattr(core.graph, "vault_changed", lambda: False)
    return j, vus


def test_repos_releve_seulement_hors_conversation_et_hors_mode_leger(monkeypatch):
    j, vus = _repos(monkeypatch)
    j._repos_tick()
    assert vus == ["releve"]
    for kw in ({"conversation": True}, {"leger": True}):
        j, vus = _repos(monkeypatch, **kw)
        j._repos_tick()
        assert vus == []


def test_repos_session_neuve_apres_inactivite(monkeypatch):
    j, vus = _repos(monkeypatch, dernier_tour=1.0)  # dernier tour il y a très longtemps
    j._repos_tick()
    assert vus == ["reset"]


def test_repos_une_panne_n_empeche_pas_le_coffre(monkeypatch):
    j, vus = _repos(monkeypatch)

    class Casse:
        def tick(self):
            raise RuntimeError("relève")
    j.relief = Casse()
    publie = []
    monkeypatch.setattr(core.graph, "vault_changed", lambda: True)
    monkeypatch.setattr(core.graph, "publish", lambda: publie.append(1))
    j._repos_tick()
    assert publie == [1]


def test_mode_texte_lance_le_fil_de_repos(monkeypatch):
    from astrel import cli

    appels = []

    class Faux:
        def boot(self):
            appels.append("boot")

        def veille_texte(self):
            appels.append("veille")

        class status:
            @staticmethod
            def set(*_a, **_k):
                pass

        def shutdown(self):
            appels.append("shutdown")

    def eof(_invite=""):
        raise EOFError
    monkeypatch.setattr("builtins.input", eof)
    cli.text_mode(Faux())
    assert appels == ["boot", "veille", "shutdown"]


def test_run_garde_le_battement_et_appelle_le_repos():
    import inspect
    src = inspect.getsource(core.Astrel.run)
    assert 'gel.chien.battre("coeur")' in src and "self._repos_tick()" in src
    assert "relief.tick" not in src  # un seul endroit : `_repos_tick`
