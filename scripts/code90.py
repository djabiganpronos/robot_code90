import requests
from bs4 import BeautifulSoup
import re
import os
import json
import csv
from datetime import datetime, timezone

# ============================================================
# CONFIGURATION
# ============================================================
# Sécurité : la clé est lue depuis la variable d'environnement ODDS_API_KEY
# (injectée par GitHub Actions via un Secret du dépôt). Ne jamais la coder
# en dur si ce dépôt est public.
API_KEY = os.environ.get("ODDS_API_KEY")
SPORT = "soccer"
URL_API = f"https://api.the-odds-api.com/v4/sports/{SPORT}/odds/?apiKey={API_KEY}&regions=eu&markets=h2h,totals&oddsFormat=decimal"
URL_SCORES = f"https://api.the-odds-api.com/v4/sports/{SPORT}/scores/?apiKey={API_KEY}&daysFrom=1"
URL_FST = "https://www.freesupertips.com/football-tips/"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_JSON = os.path.join(BASE_DIR, "docs", "data", "latest.json")
HISTORY_CSV = os.path.join(BASE_DIR, "docs", "data", "history.csv")

headers = {'User-Agent': 'Mozilla/5.0 (Linux; Android 10; Infinix Smart)'}
matchs_tendances = {}

# ============================================================
# COLLECTE DES TENDANCES PUBLIQUES
# ============================================================
try:
    rep_fst = requests.get(URL_FST, headers=headers, timeout=10)
    if rep_fst.status_code == 200:
        soup = BeautifulSoup(rep_fst.text, 'html.parser')
        for ligne in soup.get_text().split('\n'):
            if "vs" in ligne.lower():
                m = re.search(r'([A-Za-z\s]+?\svs\s[A-Za-z\s]+?)', ligne)
                if m:
                    nom_m = m.group(1).strip().lower()
                    if len(nom_m) < 50:
                        type_prono = "Over" if "Over" in ligne else ("Under" if "Under" in ligne else "1X2")
                        matchs_tendances[nom_m] = type_prono
except Exception:
    pass

# ============================================================
# COLLECTE DES SCORES EN DIRECT ET TERMINÉS (même API, un seul appel
# supplémentaire par exécution — coûte 2 crédits avec daysFrom=1)
# ============================================================
scores_par_match = {}
try:
    if API_KEY:
        rep_scores = requests.get(URL_SCORES, timeout=15)
        if rep_scores.status_code == 200:
            for ev in rep_scores.json():
                home_s = ev.get("home_team")
                away_s = ev.get("away_team")
                if not home_s or not away_s:
                    continue
                score_home = score_away = None
                if ev.get("scores"):
                    for s in ev["scores"]:
                        if s.get("name") == home_s:
                            score_home = s.get("score")
                        elif s.get("name") == away_s:
                            score_away = s.get("score")
                scores_par_match[(home_s, away_s)] = {
                    "termine": bool(ev.get("completed")),
                    "score_home": score_home,
                    "score_away": score_away,
                    "derniere_maj": ev.get("last_update"),
                }
except Exception:
    pass

# ============================================================
# TRAITEMENT PRINCIPAL — MODÈLE A UNIQUEMENT
# ============================================================
resultats = []
erreur_globale = None

if not API_KEY:
    erreur_globale = "ODDS_API_KEY non configurée (secret GitHub manquant)"
else:
    try:
        reponse = requests.get(URL_API, timeout=15)
        if reponse.status_code != 200:
            raise Exception(f"Code HTTP {reponse.status_code} : {reponse.text[:200]}")

        matchs_pros = reponse.json()

        for match in matchs_pros:
            home = match['home_team']
            away = match['away_team']
            competition = match.get('sport_title', 'Football')

            cote_home = cote_away = cote_draw = None
            cote_over = cote_under = None
            ligne_buts = 2.5

            for bookmaker in match['bookmakers']:
                for market in bookmaker['markets']:
                    if market['key'] == 'h2h':
                        for o in market['outcomes']:
                            if o['name'] == home and cote_home is None: cote_home = o['price']
                            elif o['name'] == away and cote_away is None: cote_away = o['price']
                            elif o['name'] == 'Draw' and cote_draw is None: cote_draw = o['price']
                    if market['key'] == 'totals':
                        for o in market['outcomes']:
                            if o['name'] == 'Over' and cote_over is None:
                                cote_over = o['price']
                                ligne_buts = o['point']
                            if o['name'] == 'Under' and cote_under is None:
                                cote_under = o['price']

            if not (cote_home and cote_away):
                continue

            tendance_publique = "Neutre"
            for nom_fst, prono in matchs_tendances.items():
                if home.lower()[:3] in nom_fst or away.lower()[:3] in nom_fst:
                    tendance_publique = prono
                    break

            pari, cote, confiance, motif = "", 0.0, 0, ""
            if cote_under and cote_under <= 1.85:
                pari = f"MOINS DE {ligne_buts} BUTS"
                cote = cote_under
                confiance = 88 if tendance_publique == "Over" else 82
                motif = "Verrouillage marché (seuil de cote)"
            elif cote_over and cote_over <= 1.85:
                pari = f"PLUS DE {ligne_buts} BUTS"
                cote = cote_over
                confiance = 85 if tendance_publique == "Over" else 78
                motif = "Flux offensif (seuil de cote)"
            elif cote_home and 1.35 <= cote_home <= 1.85:
                pari = f"VICTOIRE {home.upper()}"
                cote = cote_home
                confiance = 84 if cote_home < 1.50 else 78
                motif = "Cote domicile en fourchette favorite"
            elif cote_away and 1.35 <= cote_away <= 1.85:
                pari = f"VICTOIRE {away.upper()}"
                cote = cote_away
                confiance = 84 if cote_away < 1.50 else 78
                motif = "Cote extérieur en fourchette favorite"

            entree = {
                "match": f"{home} vs {away}",
                "competition": competition,
                "commence_time": match.get("commence_time"),
                "modele_a": None,
                "score": scores_par_match.get((home, away)),
            }
            if pari and confiance >= 75:
                entree["modele_a"] = {
                    "pari": pari,
                    "cote": round(cote, 2),
                    "indice_fixe": confiance,
                    "motif": motif,
                    "tendance_publique": tendance_publique,
                }

            resultats.append(entree)

        resultats.sort(key=lambda m: m.get("commence_time") or "9999")

    except Exception as e:
        erreur_globale = str(e)

# ============================================================
# ÉCRITURE DES SORTIES
# ============================================================
os.makedirs(os.path.dirname(OUTPUT_JSON), exist_ok=True)

sortie = {
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "erreur": erreur_globale,
    "nb_matchs": len(resultats),
    "matchs": resultats,
}

with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
    json.dump(sortie, f, ensure_ascii=False, indent=2)

nouveau_fichier = not os.path.exists(HISTORY_CSV)
with open(HISTORY_CSV, "a", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    if nouveau_fichier:
        writer.writerow(["date_utc", "nb_matchs", "nb_signaux_modele_a", "erreur"])
    nb_signaux = sum(1 for m in resultats if m.get("modele_a"))
    writer.writerow([sortie["generated_at"], len(resultats), nb_signaux, erreur_globale or ""])

print(f"[OK] {len(resultats)} match(s) traité(s). Résultats écrits dans {OUTPUT_JSON}")
if erreur_globale:
    print(f"[X] Erreur rencontrée : {erreur_globale}")
