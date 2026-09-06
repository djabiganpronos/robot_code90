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
APIFOOTBALL_KEY = os.environ.get("APIFOOTBALL_KEY")
SPORT = "soccer"
URL_API = f"https://api.the-odds-api.com/v4/sports/{SPORT}/odds/?apiKey={API_KEY}&regions=eu&markets=h2h,totals&oddsFormat=decimal"
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
# COLLECTE DES SCORES EN DIRECT ET TERMINÉS — API-Football
# (2 appels par exécution au total, quel que soit le nombre de ligues :
#  1) fixtures en direct, toutes ligues confondues
#  2) fixtures du jour, toutes ligues confondues, pour les matchs terminés)
# ============================================================
import unicodedata
import difflib

STATUTS_TERMINES = {"FT", "AET", "PEN", "WO", "AWD"}
STATUTS_EN_DIRECT = {"1H", "HT", "2H", "ET", "P", "BT", "SUSP", "INT", "LIVE"}

fixtures_af = []
debug_scores = {"appel_ok": False, "nb_fixtures": 0, "erreur": None}

def _normaliser(nom):
    nom = unicodedata.normalize("NFKD", nom or "").encode("ascii", "ignore").decode("ascii")
    nom = nom.replace("-", " ").replace(".", " ")
    return " ".join(nom.lower().split())

if APIFOOTBALL_KEY:
    try:
        headers_af = {"x-apisports-key": APIFOOTBALL_KEY}
        aujourdhui = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        r1 = requests.get("https://v3.football.api-sports.io/fixtures",
                           headers=headers_af, params={"live": "all"}, timeout=15)
        r2 = requests.get("https://v3.football.api-sports.io/fixtures",
                           headers=headers_af, params={"date": aujourdhui}, timeout=15)

        vus = set()
        for r in (r1, r2):
            if r.status_code == 200:
                for fx in r.json().get("response", []):
                    fid = fx.get("fixture", {}).get("id")
                    if fid and fid not in vus:
                        vus.add(fid)
                        fixtures_af.append(fx)
            else:
                debug_scores["erreur"] = f"HTTP {r.status_code} : {r.text[:150]}"

        debug_scores["appel_ok"] = True
        debug_scores["nb_fixtures"] = len(fixtures_af)
    except Exception as e:
        debug_scores["erreur"] = str(e)


def trouver_score(home_odds, away_odds):
    """Cherche la fixture API-Football correspondant au match odds-api, par
    similarité de noms (les deux API ne nomment pas toujours pareil). Ne
    retourne un résultat que si les deux équipes matchent avec confiance."""
    nh, na = _normaliser(home_odds), _normaliser(away_odds)
    for fx in fixtures_af:
        teams = fx.get("teams", {})
        nom_home_af = _normaliser(teams.get("home", {}).get("name", ""))
        nom_away_af = _normaliser(teams.get("away", {}).get("name", ""))
        sh = difflib.SequenceMatcher(None, nh, nom_home_af).ratio()
        sa = difflib.SequenceMatcher(None, na, nom_away_af).ratio()
        if sh >= 0.65 and sa >= 0.65:
            statut = fx.get("fixture", {}).get("status", {}).get("short")
            buts = fx.get("goals", {})
            if statut in STATUTS_TERMINES:
                return {"termine": True, "score_home": buts.get("home"), "score_away": buts.get("away")}
            if statut in STATUTS_EN_DIRECT:
                return {"termine": False, "score_home": buts.get("home"), "score_away": buts.get("away")}
    return None

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
                "score": trouver_score(home, away),
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
    "debug_scores": debug_scores,
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
