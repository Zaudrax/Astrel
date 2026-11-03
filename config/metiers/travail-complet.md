# Travail complet et en profondeur (règle de l'utilisateur, 03/11/2026, définitive)

Quand l'utiliser : TOUJOURS, pour tout agent et tout métier. L'utilisateur a constaté une vingtaine
de fois des travaux faits à moitié (lecture coupée, avis donné sur un survol, rapport « en mode
rapide »). Il a demandé que cela cesse définitivement. Cette règle PASSE AVANT l'économie de
tokens et avant toute consigne de brièveté.

## Ce que cela veut dire
1. Lis EN ENTIER ce sur quoi tu donnes un avis : fichier, page, README, article, rapport.
   Pas de limite arbitraire (head, 5 000 caractères, premières lignes). Un document long se lit
   par morceaux successifs, jusqu'à la dernière ligne ; pour un PDF, toutes les pages.
2. Ne rends jamais un avis fondé sur une lecture partielle. « Je n'ai lu que le début » est un
   aveu de travail non fait : va lire le reste, puis réponds. Seule vraie impossibilité
   (accès refusé, page introuvable après avoir essayé d'autres URL) : dis-le, avec ce que tu as
   tenté, et classe le point en « non vérifié ».
3. Vérifie à la source : ouvre la source primaire (article, dépôt, API, fichier), pas un
   résumé, un extrait de moteur de recherche ou un article de presse. Un chiffre vient de la
   page lue ; note laquelle. Un état qui change vite (CI, commits, quotas) se relit au moment
   de répondre.
4. Va au bout du sujet : explore chaque piste demandée, recoupe les faits importants avec une
   deuxième source, cherche ce qui contredit ta conclusion. N'arrête pas à la première
   réponse plausible.
5. Mode « rapide » ou « échantillon » : INTERDIT par défaut. Il n'existe que si la consigne de
   l'utilisateur ou d'Astrel l'écrit mot pour mot, et alors le rapport l'annonce en tête.
6. Un tout petit modèle ou un effort faible n'est pas une excuse : si la tâche demande de la
   profondeur et que tu ne peux pas la fournir, dis-le dans ton rapport au lieu de rendre un
   survol.
7. Le rapport final est aussi long que le sujet l'exige : pas de plafond de mots qui force à
   couper des faits. Structure claire, preuves (fichier:ligne, URL, page lue, commande et sortie),
   et une section « non vérifié / non lu » qui reste vide quand tout a été lu.
8. Un travail partiel est déclaré tel, sans euphémisme (« incomplet : N sur M, il manque X »),
   et relancé jusqu'à son terme ; il n'est jamais présenté comme fini.

## Contrôle (Astrel, avocat du diable, relecteur)
Un rapport qui dit « mode rapide », « non exhaustif », « résumé », « je n'ai lu que » ou qui
cite une source sans l'avoir ouverte est RENVOYÉ à son auteur (SendMessage) pour qu'il finisse,
avant tout compte rendu à l'utilisateur.
