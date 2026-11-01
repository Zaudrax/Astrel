"""Mot d'éveil « Astrel » (25/10, plan P36) : comparateur de noms (`eveil`) et `WakeWord` composite
(voie texte + alias `hey_jarvis`). Aucun modèle réel : transcription et modèle audio simulés."""
import threading
import time

import pytest

from astrel import config, core, eveil, listener
from astrel.listener import WakeWord

FRAME_S = config.FRAME_SAMPLES / config.SAMPLE_RATE  # 80 ms
NOMS = eveil.noms("Astrel")


# --------------------------------------------------------------------------- comparateur

@pytest.mark.parametrize("texte", [
    "Astrel", "Astrel.", "astral", "Astrelle ?", "A strel", "Hey Astrel", "OK, Astrel, quelle heure est-il ?",
    "Dis Astrel, mets de la musique", "Hé, Astrèl !", "Astrell", "Astel",
])
def test_nom_reconnu(texte):
    assert eveil.trouve(texte, NOMS)


@pytest.mark.parametrize("texte", [
    "", "Estelle", "Astérix et Obélix", "Mistral gagnant", "Les astres sont beaux ce soir",
    "l'hémisphère austral", "Je pense que Astrel viendra",  # nom au 4e mot : pas un appel
    "Hey Jarvis", "Bonjour tout le monde",
])
def test_pas_le_nom(texte):
    assert eveil.trouve(texte, NOMS) == ""


def test_noms_variantes_du_reglage_sans_doublon():
    assert eveil.noms("Astrel", "astral, ASTREL, , astrelle") == ["Astrel", "astral", "astrelle"]
    assert eveil.noms("Nova", ["nova", "Novah"]) == ["Nova", "Novah"]


def test_nom_court_exige_l_egalite():
    assert eveil.trouve("Max, allume", ["Max"]) == "Max"
    assert eveil.trouve("Mas, allume", ["Max"]) == ""


def test_nom_exact_l_emporte_sur_l_exclusion():
    # « astres » est écarté par la tolérance, mais pas s'il est lui-même le nom choisi
    assert eveil.trouve("Astres, bonjour", ["Astres"]) == "Astres"


def test_mots_indices_whisper():
    assert eveil.mots_indices(NOMS) == "Astrel"  # le nom affiché seul (mesuré, voir eveil.py)


def test_noms_actuels_suivent_le_nom_affiche(monkeypatch):
    monkeypatch.setenv("ASTREL_ASSISTANT_NAME", "Nova")
    assert eveil.noms_actuels()[0] == "Nova"
    assert eveil.trouve("Nova, quelle heure", eveil.noms_actuels()) == "Nova"


def test_bascule_de_mode_avec_le_nom():
    assert core.mode_phrase("Astrel, mode silencieux") == "silencieux"
    assert core.mode_phrase("OK Astral, passe en dictée") == "dictee"


# --------------------------------------------------------------------------- WakeWord, voie texte

class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeText:
    """Transcription simulée : rend `text`, garde ce qu'on lui a donné."""

    def __init__(self, text="Astrel", delay=0.0):
        self.text, self.delay, self.calls = text, delay, []

    def __call__(self, pcm, hotwords):
        self.calls.append((pcm, hotwords))
        if self.delay:
            time.sleep(self.delay)
        return self.text


def frame(i):
    return (i % 65536).to_bytes(2, "little") * (config.FRAME_BYTES // 2)


def play(wake, clock, probs, start=0, vad=0.35):
    """Rejoue des probabilités de parole (une par bloc) ; rend les indices des blocs qui réveillent."""
    fired = []
    for k, p in enumerate(probs):
        clock.t += FRAME_S
        if wake.feed(frame(start + k), 0.4, speech=p, vad=vad):
            fired.append(start + k)
    return fired


def n(seconds):
    return round(seconds / FRAME_S)


@pytest.fixture
def sync_wake():
    clock, fake = Clock(), FakeText()
    return WakeWord(legacy=False, text=True, transcribe=fake, clock=clock, sync=True), clock, fake


def test_phrase_courte_avec_le_nom_reveille(sync_wake):
    wake, clock, fake = sync_wake
    probs = [0.0] * n(1) + [0.9] * n(0.6) + [0.0] * n(1)
    fired = play(wake, clock, probs)
    assert len(fired) == 1 and wake.last_source == "texte" and wake.last_name == "Astrel"
    # réveil au blanc de 0,4 s qui suit la parole
    assert fired[0] == n(1) + n(0.6) + n(config.WAKE_TEXT_END_S) - 1
    pcm, hotwords = fake.calls[0]
    # pré-roll de 0,5 s + parole + blanc de fin
    assert len(pcm) == (n(0.5) + n(0.6) + n(config.WAKE_TEXT_END_S)) * config.FRAME_BYTES
    assert "Astrel" in hotwords


def test_phrase_sans_le_nom_ne_reveille_pas(sync_wake):
    wake, clock, fake = sync_wake
    fake.text = "Estelle arrive demain"
    assert play(wake, clock, [0.0] * 5 + [0.9] * n(0.8) + [0.0] * n(1)) == []
    assert len(fake.calls) == 1


def test_bruit_trop_court_pas_transcrit(sync_wake):
    wake, clock, fake = sync_wake
    assert play(wake, clock, [0.0] * 5 + [0.9] * 2 + [0.0] * n(1)) == []
    assert fake.calls == []


def test_longue_phrase_coupee_a_trois_secondes_puis_attend_le_blanc(sync_wake):
    wake, clock, fake = sync_wake
    fake.text = "on parlait de tout et de rien"
    play(wake, clock, [0.0] * 3 + [0.9] * n(8) + [0.0] * n(1))
    assert len(fake.calls) == 1  # une seule capture pour 8 s de parole continue
    assert len(fake.calls[0][0]) <= (n(0.5) + n(config.WAKE_TEXT_MAX_S)) * config.FRAME_BYTES
    play(wake, clock, [0.9] * n(0.6) + [0.0] * n(1))
    assert len(fake.calls) == 2  # après un blanc, la phrase suivante est de nouveau écoutée


def test_hysteresis_une_syllabe_faible_ne_coupe_pas(sync_wake):
    wake, clock, fake = sync_wake
    # 0,25 est sous le seuil (0,35) mais au-dessus du seuil de fin (0,20) : pas un blanc
    play(wake, clock, [0.0] * 3 + [0.9] * 4 + [0.25] * n(1) + [0.9] * 4 + [0.0] * n(1))
    assert len(fake.calls) == 1


def test_trou_dans_le_flux_vide_la_capture(sync_wake):
    wake, clock, fake = sync_wake
    play(wake, clock, [0.0] * 3 + [0.9] * n(0.4))
    clock.t += 2.0  # micro sourd : Astrel parlait, `feed` n'était plus appelé
    play(wake, clock, [0.0] * n(1))
    assert fake.calls == []


def test_reset_jette_la_capture_en_cours(sync_wake):
    wake, clock, fake = sync_wake
    play(wake, clock, [0.0] * 3 + [0.9] * n(0.5))
    wake.reset()
    play(wake, clock, [0.0] * n(1))
    assert fake.calls == []


def test_sans_vad_la_voie_texte_ne_capte_rien(sync_wake):
    wake, clock, fake = sync_wake
    for k in range(n(2)):
        clock.t += FRAME_S
        assert not wake.feed(frame(k), 0.4)
    assert fake.calls == []


def test_delai_entre_deux_reveils(sync_wake):
    wake, clock, fake = sync_wake
    once = [0.0] * 3 + [0.9] * n(0.4) + [0.0] * n(0.5)
    assert len(play(wake, clock, once)) == 1
    assert play(wake, clock, once) == []  # < WAKE_COOLDOWN_S
    clock.t += config.WAKE_COOLDOWN_S
    assert len(play(wake, clock, once)) == 1


def test_fil_a_part_resultat_pris_au_bloc_suivant():
    clock, fake = Clock(), FakeText(delay=0.05)
    wake = WakeWord(legacy=False, text=True, transcribe=fake, clock=clock)
    assert play(wake, clock, [0.0] * 3 + [0.9] * n(0.5) + [0.0] * n(0.4)) == []  # transcription en cours
    deadline = time.monotonic() + 3
    while not fake.calls and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.15)
    assert len(play(wake, clock, [0.0])) == 1  # le bloc suivant prend le résultat


def test_fil_a_part_resultat_trop_vieux_jete():
    clock, fake = Clock(), FakeText()
    wake = WakeWord(legacy=False, text=True, transcribe=fake, clock=clock)
    play(wake, clock, [0.0] * 3 + [0.9] * n(0.5) + [0.0] * n(0.4))
    deadline = time.monotonic() + 3
    while not fake.calls and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.1)
    clock.t += config.WAKE_TEXT_MAX_AGE_S + 0.5  # l'utilisateur a eu le temps de dire autre chose
    assert play(wake, clock, [0.0]) == []


def test_fil_a_part_reset_rend_le_resultat_caduc():
    clock, gate = Clock(), threading.Event()

    def slow(pcm, hotwords):
        gate.wait(2)
        return "Astrel"

    wake = WakeWord(legacy=False, text=True, transcribe=slow, clock=clock)
    play(wake, clock, [0.0] * 3 + [0.9] * n(0.5) + [0.0] * n(0.4))
    wake.reset()  # conversation ouverte autrement (Ctrl+Alt+J) pendant la transcription
    gate.set()
    time.sleep(0.15)
    assert play(wake, clock, [0.0] * 3) == []


def test_aucun_texte_entendu_dans_le_journal(sync_wake, caplog):
    wake, clock, fake = sync_wake
    fake.text = "mon code de carte est 1234"
    caplog.set_level("DEBUG", logger="astrel.listener")
    play(wake, clock, [0.0] * 3 + [0.9] * n(0.6) + [0.0] * n(0.5))
    assert "1234" not in caplog.text and "carte" not in caplog.text


# --------------------------------------------------------------------------- alias hey_jarvis (ancien mot d'éveil)

class FakeModel:
    def __init__(self, scores):
        self.scores, self.resets = list(scores), 0

    def predict(self, pcm):
        return {"hey_jarvis": self.scores.pop(0) if self.scores else 0.0}

    def reset(self):
        self.resets += 1


def alias_wake(monkeypatch, scores, text=False, legacy=True):
    model = FakeModel(scores)
    monkeypatch.setattr(WakeWord, "_alias", lambda self: model)
    clock = Clock()
    wake = WakeWord(legacy=legacy, text=text, transcribe=FakeText("rien"), clock=clock, sync=True)
    wake._model = model
    return wake, clock, model


def test_alias_seuil_et_patience_inchanges(monkeypatch):
    monkeypatch.setattr(config, "WAKE_PATIENCE", 2)
    wake, clock, model = alias_wake(monkeypatch, [0.5, 0.1, 0.5, 0.5, 0.0])
    fired = play(wake, clock, [0.0] * 4)
    assert fired == [3] and wake.last_source == "alias"
    assert wake.last_score == pytest.approx(0.5)


def test_alias_coupe_par_le_reglage(monkeypatch):
    monkeypatch.setattr(config, "WAKE_PATIENCE", 1)
    wake, clock, model = alias_wake(monkeypatch, [0.9] * 5, legacy=None)
    monkeypatch.setattr(listener, "_setting", lambda key, default: False if key == "wake_legacy" else default)
    clock.t += 3  # le réglage est relu toutes les 2 s
    assert play(wake, clock, [0.0] * 3) == [] and not wake.legacy and wake.last_score == 0.0
    monkeypatch.setattr(listener, "_setting", lambda key, default: True if key == "wake_legacy" else default)
    clock.t += 3
    resets = model.resets
    assert play(wake, clock, [0.0]) and wake.legacy
    assert model.resets > resets  # rallumé à chaud : tampon du modèle vidé


def test_premiere_voie_qui_parle_gagne_puis_l_autre_est_remise_a_zero(monkeypatch):
    monkeypatch.setattr(config, "WAKE_PATIENCE", 1)
    wake, clock, model = alias_wake(monkeypatch, [0.0] * 20 + [0.9], text=True)
    wake._text._transcribe = FakeText("Hey Astrel")
    probs = [0.0] * 3 + [0.9] * n(0.4) + [0.0] * n(0.4)
    fired = play(wake, clock, probs)
    assert len(fired) == 1 and wake.last_source == "texte"
    assert model.resets >= 1  # l'alias repart de zéro : la même phrase ne réveille pas deux fois


# --------------------------------------------------------------------------- réglages (lot 3)

def test_reglages_wake_names_et_wake_legacy():
    from astrel.settings import reglages
    assert reglages.value("wake_legacy") is True  # actif pendant la transition (config.WAKE_LEGACY)
    assert reglages.set("wake_names", " astral ,  astrelle ") == "astral , astrelle"
    assert eveil.noms_actuels() == ["Astrel", "astral", "astrelle"]
    assert reglages.set("wake_names", "") == config.WAKE_NAMES  # vide : variantes par défaut
    reglages.set("wake_legacy", False)
    assert listener._setting("wake_legacy", True) is False  # lu par WakeWord (à chaud)


# --------------------------------------------------------------------------- surdité signalée (lot 5)

def test_voie_texte_morte_et_alias_coupe_le_cœur_le_dit_une_fois(monkeypatch):
    """Relecture P36, fiche 5 : Whisper du mot d'éveil qui ne se charge pas ET alias coupé = plus
    aucun réveil par la voix. `WakeWord.deaf` le dit ; le cœur pose UNE notice par passage à l'état
    sourd (pas à chaque bloc de 80 ms), et de nouveau après un rallumage puis une coupure."""
    class Dead:
        def __call__(self, pcm, hint):
            return ""

        def load(self):
            raise RuntimeError("libcublas absente")

    wake = WakeWord(legacy=False, text=True, transcribe=Dead(), clock=Clock())
    deadline = time.monotonic() + 5
    while not wake._text._dead and time.monotonic() < deadline:
        time.sleep(0.01)
    assert wake._text._dead and wake.deaf
    wake._legacy_prev = True  # alias rallumé (dernier état lu)
    assert not wake.deaf

    notices = []
    monkeypatch.setattr(core.bus, "publish", lambda kind, **k: notices.append((kind, k)))
    j = core.Astrel.__new__(core.Astrel)
    j.wake = wake
    for _ in range(3):
        j._wake_deaf_check()
    assert notices == []
    wake._legacy_prev = False
    for _ in range(5):
        j._wake_deaf_check()
    assert [k["level"] for _kind, k in notices] == ["warning"]
    assert notices[0][1]["text"] == core.Astrel.WAKE_DEAF_NOTICE
    wake._legacy_prev = True
    j._wake_deaf_check()
    wake._legacy_prev = False
    j._wake_deaf_check()
    assert len(notices) == 2
    j.wake = type("W", (), {})()  # faux mot d'éveil sans `deaf` (tests du cœur) : jamais sourd
    j._wake_deaf_check()
    assert len(notices) == 2


def test_voie_texte_vivante_pas_sourd():
    wake = WakeWord(legacy=False, text=True, transcribe=FakeText("rien"), clock=Clock(), sync=True)
    assert not wake.deaf
    assert WakeWord(legacy=False, text=False, clock=Clock()).deaf  # aucune voie : sourd


# --------------------------------------------------------------------------- voie modèle (P36 lot 6)

class FakeAstrel:
    """Faux modèle entraîné : un score par bloc, puis 0."""

    def __init__(self, scores):
        self.scores, self.resets = list(scores), 0

    def predict(self, pcm):
        return {"astrel": self.scores.pop(0) if self.scores else 0.0}

    def reset(self):
        self.resets += 1


def model_wake(monkeypatch, mode, scores, text=None):
    monkeypatch.setattr(config, "WAKE_MODEL_THRESHOLD", 0.5)
    monkeypatch.setattr(config, "WAKE_MODEL_PATIENCE", 2)
    monkeypatch.setattr(config, "WAKE_MODEL_CONFIRM_THRESHOLD", 0.3)
    clock = Clock()
    fake = FakeAstrel(scores)
    wake = WakeWord(legacy=False, text=text is not None, transcribe=text, clock=clock, sync=True,
                    model_mode=mode, model=fake)
    return wake, clock, fake


def test_voie_modele_eteinte_par_defaut():
    assert config.WAKE_MODEL_MODE == "off"
    wake = WakeWord(legacy=False, text=True, transcribe=FakeText("rien"), clock=Clock(), sync=True)
    assert wake.model_mode == "off" and wake._mw is None and wake.model_score == 0.0


def test_voie_modele_ou_reveille_apres_la_patience(monkeypatch):
    wake, clock, fake = model_wake(monkeypatch, "ou", [0.6, 0.2, 0.6, 0.7, 0.9])
    fired = play(wake, clock, [0.0] * 5)
    assert fired == [3] and wake.last_source == "modele" and wake.last_name == "Astrel"
    assert fake.resets >= 1  # remis à zéro après le réveil (la même phrase ne réveille pas deux fois)


def test_voie_modele_confirme_bloque_le_texte_sans_le_modele(monkeypatch):
    wake, clock, _fake = model_wake(monkeypatch, "confirme", [0.1] * 60, text=FakeText("Astrel"))
    assert play(wake, clock, [0.0] * 3 + [0.9] * n(0.6) + [0.0] * n(1)) == []


def test_voie_modele_confirme_laisse_passer_le_texte_entendu_par_le_modele(monkeypatch):
    scores = [0.0] * 5 + [0.45] + [0.0] * 60  # au-dessus du seuil de confirmation (0,3), sous celui du réveil
    wake, clock, _fake = model_wake(monkeypatch, "confirme", scores, text=FakeText("Astrel"))
    fired = play(wake, clock, [0.0] * 3 + [0.9] * n(0.6) + [0.0] * n(1))
    assert len(fired) == 1 and wake.last_source == "texte"


def test_voie_modele_confirme_ne_reveille_pas_seule(monkeypatch):
    wake, clock, _fake = model_wake(monkeypatch, "confirme", [0.9] * 10, text=FakeText("rien"))
    assert play(wake, clock, [0.0] * 10) == []


def test_voie_modele_fichier_absent_ou_mode_inconnu(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(config, "WAKE_MODEL_FILE", tmp_path / "absent.onnx")
    caplog.set_level("WARNING", logger="astrel.listener")
    wake = WakeWord(legacy=False, text=True, transcribe=FakeText("rien"), clock=Clock(), sync=True, model_mode="ou")
    assert wake.model_mode == "off" and "absent" in caplog.text
    wake = WakeWord(legacy=False, text=True, transcribe=FakeText("rien"), clock=Clock(), sync=True,
                    model_mode="toujours", model=FakeAstrel([]))
    assert wake.model_mode == "off"
    wake = WakeWord(legacy=False, text=False, clock=Clock(), model_mode="confirme", model=FakeAstrel([]))
    assert wake.model_mode == "off"  # confirmer quoi, sans voie texte ?


def test_voie_modele_ou_seule_n_est_pas_sourde(monkeypatch):
    wake, _clock, _fake = model_wake(monkeypatch, "ou", [])
    assert not wake.deaf
    wake, _clock, _fake = model_wake(monkeypatch, "confirme", [], text=FakeText("rien"))
    wake._text._dead = True
    assert wake.deaf  # « confirme » n'écoute pas seule


def test_modele_entraine_lu_par_openwakeword():
    """Le fichier livré (s'il existe) se charge dans openWakeWord 0.4 : entrée (1, 16, 96), un score."""
    if not config.WAKE_MODEL_FILE.exists():
        pytest.skip("modèle entraîné absent")
    import numpy as np

    mw = listener._ModelWake(config.WAKE_MODEL_FILE, 0.5, 2)
    for _ in range(20):
        mw.feed(np.zeros(config.FRAME_SAMPLES, dtype=np.int16).tobytes())
    assert 0.0 <= mw.score <= 1.0 and mw.score < 0.5  # le silence ne réveille pas


def test_openwakeword_accelere_identique_au_bit_pres(monkeypatch):
    """29/10 (fluidité) : `listener._accelerer_openwakeword` ne lit que la fin du tampon de 10 s au lieu
    de le recopier en liste toutes les 80 ms ; spectrogramme et empreintes doivent rester IDENTIQUES."""
    import numpy as np
    pytest.importorskip("openwakeword")
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    from openwakeword.utils import AudioFeatures

    from astrel import listener

    listener._accelerer_openwakeword()
    origine = AudioFeatures.__dict__["_astrel_origine"]  # la version de la bibliothèque, gardée au remplacement
    a, b = AudioFeatures(), AudioFeatures()
    rapide = AudioFeatures._streaming_melspectrogram
    try:
        rng = np.random.default_rng(7)
        for _ in range(60):  # ~5 s de son, au-delà des 76 trames du premier calcul
            bloc = rng.integers(-4000, 4000, 1280, dtype=np.int16)
            AudioFeatures._streaming_melspectrogram = origine
            a(bloc)
            AudioFeatures._streaming_melspectrogram = rapide
            b(bloc)
        assert np.array_equal(a.melspectrogram_buffer, b.melspectrogram_buffer)
        assert np.array_equal(a.feature_buffer, b.feature_buffer)
    finally:
        AudioFeatures._streaming_melspectrogram = rapide
