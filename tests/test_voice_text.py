import chemins_machine  # chemins de la machine qui lance la suite
from astrel.voice.text import SentenceSplitter, clean_for_speech


def feed_all(fragments):
    sp = SentenceSplitter()
    out = []
    for f in fragments:
        out += sp.feed(f)
    return out, sp.flush()


def test_phrases_rendues_des_qu_elles_sont_finies():
    out, rest = feed_all(["Il est environ ", "18 h 51. Le build", " est passé ! Tout va bien"])
    assert out == ["Il est environ 18 h 51.", "Le build est passé !"]
    assert rest == ["Tout va bien"]


def test_decimales_et_abreviations_ne_coupent_pas():
    out, rest = feed_all(["La version 3.5 est sortie, cf. la note de M. Dupont. Fin."])
    assert out == ["La version 3.5 est sortie, cf. la note de M. Dupont."]
    assert rest == ["Fin."]


def test_fragment_trop_court_rattache_au_suivant():
    out, _ = feed_all(["Oui. ", "Je lance les tests maintenant. "])
    assert out == ["Oui. Je lance les tests maintenant."]


def test_bloc_de_code_jamais_coupe():
    sp = SentenceSplitter()
    assert sp.feed("Voici le code :\n```python\nx = 1. y = 2.\n") == ["Voici le code :"]
    assert sp.feed("```\nC'est fait, tout marche. ") == ["```python\nx = 1. y = 2.\n```", "C'est fait, tout marche."]


def test_nettoyage_du_markdown():
    text = "## Résultat\n- **3 tests** passent\n- voir [la doc](https://x.fr/doc)\n`npm test` ok"
    spoken = clean_for_speech(text)
    assert "#" not in spoken and "*" not in spoken and "`" not in spoken
    assert "3 tests passent" in spoken
    assert "la doc" in spoken and "https" not in spoken


def test_chemins_et_urls_raccourcis():
    spoken = clean_for_speech(f"J'ai modifié {chemins_machine.PROJETS}/Boutique/src/app.tsx, voir https://github.com/x/y")
    assert "app.tsx" in spoken and "/data" not in spoken
    assert "le lien" in spoken


def test_code_resume():
    assert "voir le code" in clean_for_speech("Regarde :\n```js\nconsole.log(1)\n```")


def test_troncature_longue():
    long = "Une phrase assez longue pour remplir. " * 100
    assert len(clean_for_speech(long, max_chars=300)) <= 305


def test_point_colle_devient_une_pause():
    assert clean_for_speech("Je suis Astrel.Et je vais t'aider.") == "Je suis Astrel. Et je vais t'aider."
    # Ni les noms de fichier, ni les décimales, ni les initiales.
    assert clean_for_speech("Ouvre README.md, il fait 21.5 degrés, dis-le à M.Dupont.") == (
        "Ouvre README.md, il fait 21.5 degrés, dis-le à M.Dupont."
    )
