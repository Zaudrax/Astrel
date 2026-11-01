# Règle de couverture (demande de l'utilisateur, 20/10)

Quand l'utiliser : dès que tu conclus sur une POPULATION (avis d'une appli, pages d'un
site, sources, fichiers, résultats de recherche, lignes d'un journal, tests). But : ne pas
s'arrêter à 5 ou 6 éléments quand il y en a 100.

## Mode, choisi par la consigne
- « rapide » : un échantillon, ANNONCÉ comme tel (« échantillon rapide : 20 sur 340 »),
  conclusions marquées provisoires. Mode par défaut si la consigne ne dit rien et que la
  population dépasse 200 éléments ; sinon « exhaustif ».
- « exhaustif » : toute la population (par script si elle est grande), puis lecture à la
  main d'un échantillon stratifié pour comprendre le détail.

## Méthode
1. COMPTE d'abord : combien d'éléments au total (M) ? Une commande ou une requête qui le
   donne (wc -l, nombre de pages, total affiché par le site, API). M inconnu : dis-le et
   donne ta meilleure borne, avec sa source.
2. Gros volume (plus d'une cinquantaine, centaines d'avis) : récupère les données PAR
   SCRIPT (python ou jq, fichiers dans /tmp) au lieu de les lire à la main : compter,
   répartir (par note, par date, par version, par pays), extraire les mots et thèmes
   récurrents avec leur fréquence.
3. Lis ensuite à la main un échantillon STRATIFIÉ (quelques éléments par note, par
   période, par catégorie) ou ALÉATOIRE (tirage avec une graine notée). Interdit :
   « les premiers », « les plus récents » seuls, ou ce que la page montre par défaut.
4. Arrête-toi seulement si la couverture suffit : toute la population, ou un échantillon
   dont tu justifies la taille (par strate, au moins 5 éléments ou tous s'il y en a moins ;
   une proportion qui compte, au moins 30 éléments au total).
5. Couverture insuffisante (accès bloqué, pagination cassée, temps) : écris
   « incomplet : N sur M » et ce qui manque, n'invente pas la suite.

## Ligne obligatoire dans le rapport et la passation
« Couverture : examinés N sur M (mode rapide|exhaustif ; méthode : script + échantillon
stratifié par note…) ». Une conclusion chiffrée (« la plupart », « 60 % ») cite sa base.

## Contrôle (avocat du diable, relecteur)
Un rapport qui conclut sur une population SANS cette ligne, ou qui généralise à partir des
premiers éléments, est refusé : renvoie-le avec « couverture manquante ».
