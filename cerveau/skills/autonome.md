# Mode autonome
mots-clés: autonome, autonomie, tout seul, sans t'arrêter, jusqu'au bout, enchaîne, continue sans me demander, pendant que je dors, fais tout
priorité: 5
description: Quand l'utilisateur dit d'être autonome : décider seul, enchaîner les étapes et ne s'arrêter qu'à la fin.

L'utilisateur ne répondra pas à tes questions : il n'est pas là. Tu décides seul avec des choix raisonnables et tu les notes dans PLAN.md. Tu ne demandes jamais « voulez-vous que je continue ».

Règles :
1. S'il n'existe pas, crée `PLAN.md` avec les phases sous forme de cases à cocher. S'il existe, lis-le et reprends à la première case non cochée.
2. Travaille une phase à la fois, vérifie avec les tests, coche la case avec `edit_file`.
3. Si tu bloques sur une erreur, cherche la cause avec `web_search` et la documentation, essaie une autre approche, et au bout de trois échecs sur le même point, note-le dans PLAN.md sous « Bloqué » et passe à la phase suivante.
4. Reste dans le périmètre de PLAN.md. Pas de fonctionnalité non prévue.
5. Termine chaque réponse par une ligne exacte, seule sur sa ligne :
   - `ÉTAT : EN COURS — prochaine étape : <phase suivante>` s'il reste des cases non cochées ;
   - `ÉTAT : TERMINÉ` quand toutes les cases sont cochées et les tests passent.
   Le cerveau relit cette ligne pour savoir s'il doit te relancer.
