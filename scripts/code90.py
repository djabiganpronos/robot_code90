import requests
from bs4 import BeautifulSoup
import re
import math
import os
import json
import csv
from datetime import datetime, timezone

# ============================================================
# CONFIGURATION
# ============================================================
# Sécurité : la clé est lue depuis la variable d'environnement ODDS_API_KEY
# (injectée par GitHub Actions via un Secret du dépôt). Le fallback codé en
# dur ci-dessous permet de tester en local, mais NE DOIT PAS être utilisé
# tel quel si ce dépôt est public : une clé API visible dans le code source
# d'un repo public sera récupérée et utilisée par d'autres personnes.
API_KEY = os.environ.get("ODDS_API_KEY", "43e94aa79dc4ac16f85537e11b6a5b17")
SPORT = "soccer"
URL_API = f"https://api.the-odds-api.com/v4/sports/{SPORT}/odds/?apiKey={API_KEY}&regions=eu&markets=h2h,totals&oddsFormat=decimal"
URL_FST = "https://www.freesupertips.com/football-tips/"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_JSON = os.path.join(BASE_DIR, "docs", "data", "latest.json")
HISTORY_CSV = os.path.join(BASE_DIR, "docs", "data", "history.csv")

headers = {'User-Agent': 'Mozilla/5.0 (Linux; Android 10; Infinix Smart)'}
matchs_tendances = {}

# ============================================================
# COLLECTE DES TENDANCES PUBLIQUES (Modèle A)
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
# OUTILS STATISTIQUES (Modèle B)
# ============================================================

def devig_2way(o1, o2):
    if not o1 or not o2:
        return None, None
    imp1, imp2 = 1 / o1, 1 / o2
    overround = imp1 + imp2
    return imp1 / overround, imp2 / overround


def devig_3way(o_home, o_draw, o_away):
    if not o_home or not o_draw or not o_away:
        return None, None, None
    imp = [1 / o_home, 1 / o_draw, 1 / o_away]
    overround = sum(imp)
    return imp[0] / overround, imp[1] / overround, imp[2] / overround


def poisson_pmf(k, lam):
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def poisson_1x2(lam_home, lam_away, max_goals=8):
    p_home, p_draw, p_away = 0.0, 0.0, 0.0
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            p = poisson_pmf(i, lam_home) * poisson_pmf(j, lam_away)
            if i > j:
                p_home += p
            elif i == j:
                p_draw += p
            else:
                p_away += p
    return p_home, p_draw, p_away


def poisson_over(lam_home, lam_away, ligne):
    lam_total = lam_home + lam_away
    seuil = math.floor(ligne)
    p_under_ou_egal = sum(poisson_pmf(k, lam_total) for k in range(seuil + 1))
    return 1 - p_under_ou_egal


def _erreur_ajustement(lh, la, fair_home, fair_draw, fair_away, fair_over, ligne):
    ph, pd, pa = poisson_1x2(lh, la, max_goals=8)
    po = poisson_over(lh, la, ligne)
    erreur = (ph - fair_home) ** 2 + (pd - fair_draw) ** 2 + (pa - fair_away) ** 2
    if fair_over is not None:
        erreur += (po - fair_over) ** 2
    return erreur


def estimer_lambdas(fair_home, fair_draw, fair_away, fair_over, ligne):
    """
    Reconstruit lambda_home / lambda_away avec DEUX paramètres indépendants
    (pas de somme totale imposée), calés simultanément sur les 4 probabilités
    de marché dévigées (victoire dom/nul/ext + over/under). Recherche par
    grille en deux passes (grossière puis affinée) : plus robuste que la
    version à un seul paramètre, qui ne pouvait pas bien ajuster les matchs
    déséquilibrés (biais systématique observé sur les gros outsiders).
    """
    if fair_home is None:
        return None, None

    # Passe 1 : grille grossière
    meilleure_erreur = None
    meilleur_lh, meilleur_la = 1.3, 1.3
    lh = 0.2
    while lh <= 4.0:
        la = 0.2
        while la <= 4.0:
            erreur = _erreur_ajustement(lh, la, fair_home, fair_draw, fair_away, fair_over, ligne)
            if meilleure_erreur is None or erreur < meilleure_erreur:
                meilleure_erreur = erreur
                meilleur_lh, meilleur_la = lh, la
            la += 0.2
        lh += 0.2

    # Passe 2 : affinage autour du meilleur point trouvé
    lh_min, lh_max = max(0.05, meilleur_lh - 0.2), meilleur_lh + 0.2
    la_min, la_max = max(0.05, meilleur_la - 0.2), meilleur_la + 0.2
    lh = lh_min
    while lh <= lh_max:
        la = la_min
        while la <= la_max:
            erreur = _erreur_ajustement(lh, la, fair_home, fair_draw, fair_away, fair_over, ligne)
            if erreur < meilleure_erreur:
                meilleure_erreur = erreur
                meilleur_lh, meilleur_la = lh, la
            la += 0.02
        lh += 0.02

    return meilleur_lh, meilleur_la


def analyser_v10(home, away, competition, value_bets, api_key):
    """
    Version payante (API Anthropic + recherche web) : narrative qualitative
    complète. Reste inactive et gratuite tant que ANTHROPIC_API_KEY n'est
    pas configuré (voir logique d'appel plus bas).
    """
    if not api_key:
        return None

    marches_txt = ", ".join(f"{v['marche']} @ {v['cote']} (EV {v['ev_pct']}%)" for v in value_bets)

    prompt = (
        f"Analyste sportif expert. Match : {home} vs {away} ({competition}).\n"
        f"Un modèle quantitatif basé sur les cotes de marché détecte une value sur : {marches_txt}.\n\n"
        "Fais une recherche web pour vérifier les données réelles récentes de ce match : "
        "forme des deux équipes (5 derniers matchs), blessures/absences clés, "
        "compositions probables, contexte (enjeu, motivation). "
        "Si tu ne trouves pas d'information fiable et récente, dis-le clairement plutôt que de deviner.\n\n"
        "Conclus obligatoirement par une ligne commençant par 'VERDICT: ' suivie de "
        "RENFORCE, NUANCE ou CONTREDIT (le signal quantitatif), puis une justification "
        "en 3-4 phrases maximum basée uniquement sur des faits trouvés. "
        "Réponds en français. 200 mots maximum."
    )

    try:
        reponse = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-5",
                "max_tokens": 700,
                "system": (
                    "Tu ne cites jamais de texte source mot pour mot au-delà de quelques mots. "
                    "Tu distingues clairement les faits vérifiés des suppositions. "
                    "Tu ne forces jamais une conclusion si les données sont insuffisantes."
                ),
                "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 4}],
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        if reponse.status_code != 200:
            return {"erreur": f"HTTP {reponse.status_code}"}

        data = reponse.json()
        texte = "".join(
            bloc.get("text", "") for bloc in data.get("content", []) if bloc.get("type") == "text"
        ).strip()

        verdict = "INDÉTERMINÉ"
        for mot in ("RENFORCE", "NUANCE", "CONTREDIT"):
            if f"VERDICT: {mot}" in texte or f"VERDICT : {mot}" in texte:
                verdict = mot
                break

        return {"verdict": verdict, "analyse": texte, "source": "IA (recherche web)"}
    except Exception as e:
        return {"erreur": str(e)}


# ============================================================
# MODÈLE C GRATUIT — API-Football (données réelles + règles transparentes)
# ============================================================
import unicodedata
import difflib

AF_BASE = "https://v3.football.api-sports.io"
_af_cache_equipes = {}
_af_budget = {"restant": 92}  # marge de sécurité sous la limite gratuite de 100/jour


def _af_requete(endpoint, params, api_key):
    """Point de passage unique pour toutes les requêtes API-Football, pour
    pouvoir compter et plafonner la consommation du quota gratuit."""
    if _af_budget["restant"] <= 0:
        return None
    try:
        r = requests.get(f"{AF_BASE}/{endpoint}", headers=_af_headers(api_key), params=params, timeout=10)
        _af_budget["restant"] -= 1
        return r.json().get("response", [])
    except Exception:
        _af_budget["restant"] -= 1
        return None


def _af_normaliser(nom):
    nom = unicodedata.normalize("NFKD", nom).encode("ascii", "ignore").decode("ascii")
    return nom.lower().strip()


def _af_headers(api_key):
    return {"x-apisports-key": api_key}


def _af_trouver_equipe(nom, api_key):
    """Cherche l'ID API-Football correspondant à un nom d'équipe, avec un
    seuil de confiance minimum. Retourne None si aucune correspondance fiable."""
    cle = _af_normaliser(nom)
    if cle in _af_cache_equipes:
        return _af_cache_equipes[cle]

    try:
        candidats = _af_requete("teams", {"search": nom}, api_key) or []
    except Exception:
        candidats = []

    meilleur_id, meilleur_score = None, 0.0
    for c in candidats:
        nom_candidat = c.get("team", {}).get("name", "")
        score = difflib.SequenceMatcher(None, cle, _af_normaliser(nom_candidat)).ratio()
        if score > meilleur_score:
            meilleur_score = score
            meilleur_id = c.get("team", {}).get("id")

    resultat = meilleur_id if meilleur_score >= 0.72 else None
    _af_cache_equipes[cle] = resultat
    return resultat


def _af_forme_recente(team_id, api_key):
    """Points (max 15) sur les 5 derniers matchs joués, + différence de buts."""
    fixtures = _af_requete("fixtures", {"team": team_id, "last": 5}, api_key)
    if not fixtures:
        return None

    points, diff_buts = 0, 0
    for f in fixtures:
        buts_home = f.get("goals", {}).get("home")
        buts_away = f.get("goals", {}).get("away")
        if buts_home is None or buts_away is None:
            continue
        est_domicile = f.get("teams", {}).get("home", {}).get("id") == team_id
        bp = buts_home if est_domicile else buts_away
        bc = buts_away if est_domicile else buts_home
        diff_buts += (bp - bc)
        if bp > bc:
            points += 3
        elif bp == bc:
            points += 1
    return {"points": points, "diff_buts": diff_buts, "matchs_trouves": len(fixtures)}


def _af_prochain_match(team_id, api_key):
    fixtures = _af_requete("fixtures", {"team": team_id, "next": 1}, api_key)
    return fixtures[0]["fixture"]["id"] if fixtures else None


def _af_blessures(fixture_id, api_key):
    if not fixture_id:
        return None
    return _af_requete("injuries", {"fixture": fixture_id}, api_key)


def analyser_v10_gratuit(home, away, lam_h, lam_a, api_key):
    """
    Version gratuite : pas d'IA, pas de recherche web. Compare la forme réelle
    récente (API-Football, plan gratuit) à l'écart de force déjà implicite
    dans le Modèle B (lam_h vs lam_a). Règle transparente et documentée :
    - même sens (l'équipe favorisée par le modèle est aussi en meilleure
      forme réelle) => RENFORCE
    - sens opposé avec écart de forme significatif (>= 4 points sur 15)
      => CONTREDIT
    - sinon => NUANCE
    Les blessures ne sont pas notées automatiquement (trop de variables
    selon le poste/l'importance du joueur) : elles sont listées telles
    quelles pour que tu juges toi-même leur impact.
    """
    id_home = _af_trouver_equipe(home, api_key)
    id_away = _af_trouver_equipe(away, api_key)
    if not id_home or not id_away:
        if _af_budget["restant"] <= 0:
            return {"erreur": "quota API-Football du jour épuisé"}
        return {"erreur": "Correspondance d'équipe non trouvée avec confiance suffisante"}

    forme_home = _af_forme_recente(id_home, api_key)
    forme_away = _af_forme_recente(id_away, api_key)
    if not forme_home or not forme_away:
        return {"erreur": "Données de forme indisponibles (plan gratuit limité ou compétition non couverte)"}

    fixture_id = _af_prochain_match(id_home, api_key)
    blessures = _af_blessures(fixture_id, api_key) or []
    noms_blesses_home = [b["player"]["name"] for b in blessures if b.get("team", {}).get("id") == id_home]
    noms_blesses_away = [b["player"]["name"] for b in blessures if b.get("team", {}).get("id") == id_away]

    ecart_modele = lam_h - lam_a  # positif = modèle favorise domicile
    ecart_forme = forme_home["points"] - forme_away["points"]  # positif = domicile en meilleure forme réelle

    meme_sens = (ecart_modele > 0 and ecart_forme > 0) or (ecart_modele < 0 and ecart_forme < 0)
    ecart_significatif = abs(ecart_forme) >= 4

    if meme_sens and ecart_significatif:
        verdict = "RENFORCE"
    elif not meme_sens and ecart_significatif:
        verdict = "CONTREDIT"
    else:
        verdict = "NUANCE"

    return {
        "verdict": verdict,
        "source": "Données réelles (API-Football, gratuit)",
        "forme_home": f"{forme_home['points']}/15 pts (diff. buts {forme_home['diff_buts']:+d}) sur {forme_home['matchs_trouves']} matchs",
        "forme_away": f"{forme_away['points']}/15 pts (diff. buts {forme_away['diff_buts']:+d}) sur {forme_away['matchs_trouves']} matchs",
        "blesses_home": noms_blesses_home[:5],
        "blesses_away": noms_blesses_away[:5],
    }


# ============================================================
# TRAITEMENT PRINCIPAL
# ============================================================
resultats = []
erreur_globale = None
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
APIFOOTBALL_KEY = os.environ.get("APIFOOTBALL_KEY")

try:
    reponse = requests.get(URL_API, timeout=15)
    if reponse.status_code != 200:
        raise Exception(f"Code HTTP {reponse.status_code} : {reponse.text[:200]}")

    matchs_pros = reponse.json()

    for match in matchs_pros:
        home = match['home_team']
        away = match['away_team']
        competition = match.get('sport_title', 'Football')

        pin_home = pin_draw = pin_away = None
        pin_over = pin_under = None
        pub_home_l, pub_draw_l, pub_away_l = [], [], []
        pub_over_l, pub_under_l = [], []
        ligne_buts = 2.5

        for bookmaker in match['bookmakers']:
            is_pinnacle = bookmaker['key'] == 'pinnacle'
            for market in bookmaker['markets']:
                if market['key'] == 'h2h':
                    oh = od = oa = None
                    for o in market['outcomes']:
                        if o['name'] == home: oh = o['price']
                        elif o['name'] == away: oa = o['price']
                        elif o['name'] == 'Draw': od = o['price']
                    if is_pinnacle:
                        pin_home, pin_draw, pin_away = oh, od, oa
                    else:
                        if oh: pub_home_l.append(oh)
                        if od: pub_draw_l.append(od)
                        if oa: pub_away_l.append(oa)
                if market['key'] == 'totals':
                    for o in market['outcomes']:
                        if o['name'] == 'Over':
                            if is_pinnacle:
                                pin_over = o['price']
                            else:
                                pub_over_l.append(o['price'])
                            ligne_buts = o['point']
                        if o['name'] == 'Under':
                            if is_pinnacle:
                                pin_under = o['price']
                            else:
                                pub_under_l.append(o['price'])

        pub_home = sum(pub_home_l) / len(pub_home_l) if pub_home_l else None
        pub_draw = sum(pub_draw_l) / len(pub_draw_l) if pub_draw_l else None
        pub_away = sum(pub_away_l) / len(pub_away_l) if pub_away_l else None
        pub_over = sum(pub_over_l) / len(pub_over_l) if pub_over_l else None
        pub_under = sum(pub_under_l) / len(pub_under_l) if pub_under_l else None

        cote_home = pin_home or pub_home
        cote_away = pin_away or pub_away
        cote_draw = pin_draw or pub_draw
        cote_over = pin_over or pub_over
        cote_under = pin_under or pub_under

        if not (cote_home and cote_away):
            continue

        entree = {
            "match": f"{home} vs {away}",
            "competition": competition,
            "commence_time": match.get("commence_time"),
            "modele_a": None,
            "modele_b": None,
            "pinnacle_disponible": bool(pin_home),
        }

        # --- Modèle A (inchangé) ---
        tendance_publique = "Neutre"
        for nom_fst, prono in matchs_tendances.items():
            if home.lower()[:3] in nom_fst or away.lower()[:3] in nom_fst:
                tendance_publique = prono
                break

        pari_a, cote_a, confiance_a = "", 0.0, 0
        if cote_under and cote_under <= 1.85:
            pari_a = f"MOINS DE {ligne_buts} BUTS"
            cote_a = cote_under
            confiance_a = 88 if tendance_publique == "Over" else 82
        elif cote_over and cote_over <= 1.85:
            pari_a = f"PLUS DE {ligne_buts} BUTS"
            cote_a = cote_over
            confiance_a = 85 if tendance_publique == "Over" else 78
        elif cote_home and 1.35 <= cote_home <= 1.85:
            pari_a = f"VICTOIRE {home.upper()}"
            cote_a = cote_home
            confiance_a = 84 if cote_home < 1.50 else 78
        elif cote_away and 1.35 <= cote_away <= 1.85:
            pari_a = f"VICTOIRE {away.upper()}"
            cote_a = cote_away
            confiance_a = 84 if cote_away < 1.50 else 78

        if pari_a and confiance_a >= 75:
            entree["modele_a"] = {"pari": pari_a, "cote": round(cote_a, 2), "indice_fixe": confiance_a}

        # --- Modèle B (EV réel) ---
        fh, fd, fa = devig_3way(cote_home, cote_draw, cote_away)
        fo, fu = devig_2way(cote_over, cote_under) if (cote_over and cote_under) else (None, None)

        if fh is not None and fo is not None:
            lam_h, lam_a = estimer_lambdas(fh, fd, fa, fo, ligne_buts)
            if lam_h is not None:
                p_home_mod, p_draw_mod, p_away_mod = poisson_1x2(lam_h, lam_a)
                p_over_mod = poisson_over(lam_h, lam_a, ligne_buts)
                p_under_mod = 1 - p_over_mod

                # Si Pinnacle est disponible, on mélange l'estimation Poisson
                # avec sa probabilité dévigée (référence sharp) pour réduire
                # l'impact d'un mauvais ajustement du modèle sur un seul match.
                if pin_home and pin_draw and pin_away:
                    fh_pin, fd_pin, fa_pin = devig_3way(pin_home, pin_draw, pin_away)
                    if fh_pin is not None:
                        p_home_mod = (p_home_mod + fh_pin) / 2
                        p_draw_mod = (p_draw_mod + fd_pin) / 2
                        p_away_mod = (p_away_mod + fa_pin) / 2
                if pin_over and pin_under:
                    fo_pin, fu_pin = devig_2way(pin_over, pin_under)
                    if fo_pin is not None:
                        p_over_mod = (p_over_mod + fo_pin) / 2
                        p_under_mod = (p_under_mod + fu_pin) / 2

                marches = [
                    ("VICTOIRE " + home.upper(), p_home_mod, pub_home or pin_home),
                    ("NUL", p_draw_mod, pub_draw or pin_draw),
                    ("VICTOIRE " + away.upper(), p_away_mod, pub_away or pin_away),
                    (f"PLUS DE {ligne_buts} BUTS", p_over_mod, pub_over or pin_over),
                    (f"MOINS DE {ligne_buts} BUTS", p_under_mod, pub_under or pin_under),
                ]
                meilleurs = []
                for nom_marche, proba, cote in marches:
                    if not cote:
                        continue
                    ev = (proba * cote) - 1
                    if ev > 0:
                        meilleurs.append({
                            "marche": nom_marche,
                            "cote": round(cote, 2),
                            "probabilite_modele": round(proba * 100, 1),
                            "ev_pct": round(ev * 100, 1),
                        })
                meilleurs.sort(key=lambda x: x["ev_pct"], reverse=True)

                divergence = None
                if pin_home and pub_home:
                    fh_pin, _, _ = devig_3way(pin_home, pin_draw, pin_away)
                    fh_pub, _, _ = devig_3way(pub_home, pub_draw, pub_away)
                    if fh_pin and fh_pub:
                        divergence = round((fh_pin - fh_pub) * 100, 1)

                entree["modele_b"] = {
                    "lambda_domicile": round(lam_h, 2),
                    "lambda_exterieur": round(lam_a, 2),
                    "divergence_pinnacle_public_pct": divergence,
                    "value_bets": meilleurs[:3],
                }

                # Analyse V10 qualitative, uniquement si une value a été détectée.
                # Priorité à la version IA (payante) si configurée, sinon
                # bascule automatiquement sur la version gratuite API-Football.
                if meilleurs:
                    if ANTHROPIC_API_KEY:
                        entree["modele_c"] = analyser_v10(home, away, competition, meilleurs[:3], ANTHROPIC_API_KEY)
                    elif APIFOOTBALL_KEY:
                        entree["modele_c"] = analyser_v10_gratuit(home, away, lam_h, lam_a, APIFOOTBALL_KEY)

        resultats.append(entree)

    # Tri par heure de coup d'envoi croissante (les matchs sans horodatage vont en fin de liste)
    resultats.sort(key=lambda m: m.get("commence_time") or "9999")

except Exception as e:
    erreur_globale = str(e)

# ============================================================
# ÉCRITURE DES SORTIES (JSON pour le dashboard + historique CSV)
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
        writer.writerow(["date_utc", "nb_matchs", "nb_value_bets_modele_b", "erreur"])
    nb_value_bets = sum(len(m["modele_b"]["value_bets"]) for m in resultats if m.get("modele_b"))
    writer.writerow([sortie["generated_at"], len(resultats), nb_value_bets, erreur_globale or ""])

print(f"[OK] {len(resultats)} match(s) traité(s). Résultats écrits dans {OUTPUT_JSON}")
if erreur_globale:
    print(f"[X] Erreur rencontrée : {erreur_globale}")
