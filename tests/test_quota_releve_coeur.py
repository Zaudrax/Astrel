"""22/10, correction n°2 du relevé du quota (mission collage-quota-2210), partie CŒUR (`astrel/core.py`) :
relevé à 100 % sans tour « point de reprise », relevé lent qui n'écrase pas le flux, un seul point de
reprise (et une seule carte d'offre) quand l'heure du texte de /usage et celle du flux diffèrent d'une
minute. Correctif décrit dans runtime/equipe/collage-quota-2210/patch-core-quota.md : déjà appliqué
dans core.py (avant le recadrage « un seul écrivain ») ; si le propriétaire de core.py le retire, ces
tests échouent, c'est voulu."""
import time
from datetime import datetime

from astrel import core, quota_releve
from astrel.brain import BrainEvent
from test_offre_quota import frais, quota  # noqa: F401  (fixture, aide)
from test_quota_releve import FIVE, NOW, PARIS, USAGE, WEEK
from test_reports import ch, astrel, of, published  # noqa: F401  (fixtures)


def _texte_plein(reset: float) -> str:
    d = datetime.fromtimestamp(reset, PARIS)
    mois = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()[d.month - 1]  # pas de %b : il suit la locale
    quand = f"{mois} {d.day}, {(d.hour % 12) or 12}:{d.minute:02d}{'pm' if d.hour >= 12 else 'am'}"
    return (f"Current session: 100% used · resets {quand} (Europe/Paris)\n"
            "Current week (all models): 63% used · resets Oct 24, 10:59am (Europe/Paris)")


def test_releve_a_100_pour_cent_ferme_la_reprise_sans_tour(astrel, published, monkeypatch):
    """Relevé au repos, fenêtre de 5 h pleine : la reprise est programmée comme pour le flux, AUCUN
    tour « point de reprise » (il échouerait : carillon d'erreur et erreur dite à voix haute)."""
    j, said, chimes = astrel
    handled = []
    monkeypatch.setattr(j, "handle", lambda text, source="voix", **k: handled.append(source))
    reset = (int(time.time()) // 60 + 120) * 60  # dans deux heures, minute pleine
    monkeypatch.setattr(quota_releve, "relever", lambda connu=None, **k: {
        "ok": True, "source": "usage", "jetons": 0, "info": quota_releve.lire_usage(_texte_plein(reset), None, connu)})
    assert j._quota_refresh()["ok"]
    assert handled == [] and "error" not in chimes and said == []
    assert core.reprise.state.closed() and core.reprise.state.info()["reset_at"] == reset
    assert j.brain.quota["status"] == "rejected" and j.brain.quota["limit_kind"] == "cinq_heures"
    assert of(published, "quota")[-1]["limited"] is True


def test_limite_sans_heure_connue_pas_de_point_de_reprise(astrel, monkeypatch):
    j, said, chimes = astrel
    handled = []
    monkeypatch.setattr(j, "handle", lambda text, source="voix", **k: handled.append(source))
    info = quota_releve.lire_usage("Current session: 100% used\nCurrent week (all models): 20% used", NOW)
    j._on_brain_event(BrainEvent("quota", data=j.brain.apply_rate_limit(info)))
    assert handled == [] and "error" not in chimes  # le cerveau refuserait le tour


def test_releve_lent_n_ecrase_pas_un_quota_plus_recent(astrel, published, monkeypatch):
    """Le relevé dure 30 à 60 s ; un `rate_limit_event` arrivé pendant ce temps fait foi."""
    j, _, _ = astrel
    flux = {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "resetsAt": FIVE, "rateLimitType": "five_hour",
            "unifiedWindows": {"five_hour": {"utilization": 0.50, "resetsAt": FIVE}, "seven_day": {"utilization": 0.70, "resetsAt": WEEK}}}}

    def lent(connu=None, **k):
        for out in j.brain._translate(flux, {}, False):  # un tour d'Astrel pendant le relevé
            j._on_brain_event(out)
        return {"ok": True, "source": "usage", "jetons": 0, "info": quota_releve.lire_usage(USAGE, NOW, connu)}
    monkeypatch.setattr(quota_releve, "relever", lent)
    r = j._quota_refresh()
    assert r["ok"] and r.get("stale") is True
    assert j.brain.quota["windows"]["five_hour"]["utilization"] == 0.50
    assert [w["pct"] for w in of(published, "quota")[-1]["windows"] if w["name"] == "five_hour"] == [50]
    assert len(of(published, "quota")) == 1  # le relevé périmé n'est pas publié


def test_un_seul_point_de_reprise_si_heures_decalees(astrel, monkeypatch):
    """Après une relance : relevé d'abord (20:19 du texte), puis le flux (20:20) ; même fenêtre."""
    j, _, _ = astrel
    handled = []
    monkeypatch.setattr(j, "handle", lambda text, source="voix", **k: handled.append(source))
    texte = USAGE.replace("Current session: 3%", "Current session: 90%")
    monkeypatch.setattr(quota_releve, "relever", lambda connu=None, **k: {
        "ok": True, "source": "usage", "jetons": 0, "info": quota_releve.lire_usage(texte, NOW, connu)})
    assert j._quota_refresh()["ok"]
    assert handled == ["réflexe"] and j.brain.quota["resets_at"] == FIVE - 60
    ev = {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "resetsAt": FIVE, "rateLimitType": "five_hour",
          "unifiedWindows": {"five_hour": {"utilization": 0.91, "resetsAt": FIVE}}}}
    for out in j.brain._translate(ev, {}, False):
        j._on_brain_event(out)
    assert handled == ["réflexe"]  # 60 s d'écart : même fenêtre
    ev["rate_limit_info"]["resetsAt"] = FIVE + 5 * 3600  # fenêtre suivante : nouveau point de reprise
    ev["rate_limit_info"]["unifiedWindows"]["five_hour"]["resetsAt"] = FIVE + 5 * 3600
    for out in j.brain._translate(ev, {}, False):
        j._on_brain_event(out)
    assert handled == ["réflexe", "réflexe"]


def test_une_seule_carte_d_offre_si_heures_decalees(astrel, frais, monkeypatch):
    """Même cause pour la carte « Passer sur Haiku ? » (clé `resets_at` de `_offer_model`)."""
    j, _, _ = astrel
    j.brain.model = "sonnet"
    monkeypatch.setattr(j, "handle", lambda *a, **k: None)
    j._guard_quota(quota(80, resets=str(FIVE - 60)))
    (carte,) = frais.pending()
    j._guard_quota(quota(81, resets=str(FIVE)))
    assert [c["id"] for c in frais.pending()] == [carte["id"]]  # 60 s d'écart : même fenêtre, pas de 2e carte
    j._guard_quota(quota(82, resets=str(FIVE + 5 * 3600)))
    assert [c["id"] for c in frais.pending()] != [carte["id"]]  # fenêtre suivante : nouvelle carte


def test_meme_fenetre_tolerance():
    same = core.Astrel._same_window
    assert same("1792693200", "1792693140") and same("1792693200", 1792693200)
    assert not same("1792693200", "1792692800")  # 400 s : autre fenêtre
    assert not same("", "1792693200") and not same("1792693200", None) and same("", "")


def test_mode_leger_pas_de_point_de_reprise_gele(astrel, monkeypatch):
    """Relecture du quota (fiche 6) : en mode léger (modèle local), le relevé au repos continue et lit
    90 % ; le tour « point de reprise » était GELÉ puis rejoué, plein contexte, au retour sur Claude
    (le flux, coupé en local, ne le provoquait jamais). Rien n'est demandé ni gelé, et la fenêtre
    n'est pas marquée « faite » : de retour sur Claude, le flux la traitera."""
    j, _, _ = astrel
    frozen = []
    monkeypatch.setattr(j, "_local_freeze", lambda text, source, channel, shown: frozen.append(source))
    j.brain.local = "qwen2.5"
    texte = USAGE.replace("Current session: 3%", "Current session: 90%")
    monkeypatch.setattr(quota_releve, "relever", lambda connu=None, **k: {
        "ok": True, "source": "usage", "jetons": 0, "info": quota_releve.lire_usage(texte, NOW, connu)})
    assert j._quota_refresh()["ok"]
    assert frozen == [] and not j._local_frozen and getattr(j, "_quota_saved_for", None) is None
