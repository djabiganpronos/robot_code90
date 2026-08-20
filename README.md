# Robot Code 90 — Exécution automatique + Dashboard

## Ce que fait ce dépôt
- `scripts/code90.py` : ton script d'analyse (Modèle A heuristique + Modèle B EV réel), écrit ses résultats dans `docs/data/latest.json` et journalise l'historique dans `docs/data/history.csv`.
- `.github/workflows/run.yml` : exécute le script automatiquement toutes les 6h (et à la demande), commit les résultats.
- `docs/index.html` : dashboard statique qui lit `docs/data/latest.json` et affiche les deux modèles côte à côte.

## Mise en place (une seule fois)

1. **Crée un dépôt GitHub** (privé de préférence, vu le contenu) et pousse ces fichiers dedans.

2. **Ajoute ta clé API en secret** (jamais en clair dans le code d'un repo, même privé) :
   - Repo → Settings → Secrets and variables → Actions → New repository secret
   - Nom : `ODDS_API_KEY`
   - Valeur : ta clé the-odds-api

3. **Active GitHub Pages** :
   - Repo → Settings → Pages
   - Source : `Deploy from a branch`
   - Branch : `main`, dossier `/docs`
   - Le dashboard sera accessible à `https://<ton-user>.github.io/<nom-du-repo>/`

4. **Lance une première exécution manuelle** pour générer `docs/data/latest.json` :
   - Repo → Actions → "Robot Code 90 - Analyse et publication" → Run workflow

## Ajuster la fréquence
Dans `.github/workflows/run.yml`, modifie la ligne cron :
```yaml
- cron: "0 */6 * * *"   # toutes les 6h
