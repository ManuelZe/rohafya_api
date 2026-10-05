"""Extraction des résultats d'analyses contenus dans un compte rendu PDF.

Méthode (voir Documentation_SaaS/extraction-pdf/EXTRACTION_RESULTATS_PDF.txt) :
  1. règles indépendantes de la mise en page : chaque ligne du texte est confrontée au
     dictionnaire des analyses (analyses_pdf.json) ; normes, unité et valeur sont lues où
     qu'elles soient dans la ligne, puis converties dans l'unité de référence ;
  2. en repli, si l'établissement l'autorise et si le serveur a une clé Anthropic : lecture
     du PDF par Claude, qui RECOPIE les lignes imprimées ; ces lignes repassent par l'étape 1 ;
  3. contrôles : analyse connue, valeur présente dans le texte du PDF, valeur plausible,
     « hors norme » recalculé, en-tête complet. Le moindre doute -> statut « a_relire ».

Rien n'est jamais publié automatiquement : un administrateur valide chaque compte rendu.
"""
import base64
import io
import json
import os
import re
import unicodedata
from functools import lru_cache

from .services import SaasError

MAX_PDF_BYTES = 10 * 1024 * 1024
STATUS_READY = "pret"
STATUS_REVIEW = "a_relire"
STATUS_PUBLISHED = "publie"
STATUS_REJECTED = "rejete"
STATUS_ERROR = "erreur"

NOMBRE = r"(?<![a-z0-9.])\d+(?:\.\d+)?(?![a-z0-9])"
INTERVALLE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-|–|a)\s*(\d+(?:\.\d+)?)")
BORNE = re.compile(r"([<>])\s*=?\s*(\d+(?:\.\d+)?)")
MARQUEUR = re.compile(r"(\*|(?<![a-z0-9])(?:h|l|bas|haut|eleve|elevee)(?![a-z0-9])|↑|↓)")
ENTETE = re.compile(r"dossier|matricule|\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2}")
DOSSIER = re.compile(
    r"(?:n[°o]\s*(?:de\s*)?dossier|ref\.?\s*dossier|dossier|matricule|id\.?\s*patient|n[°o]\s*patient)"
    r"\s*(?:n[°o]|numero)?\s*[:#]?\s*([a-z0-9][a-z0-9/_.-]{2,30})"
)
DATE_VALIDATION = re.compile(r"valid\w*[^0-9]{0,30}(\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2})")


@lru_cache(maxsize=1)
def dictionnaire():
    chemin = os.path.join(os.path.dirname(__file__), "analyses_pdf.json")
    with open(chemin, encoding="utf-8") as f:
        return json.load(f)["analyses"]


def normaliser(texte):
    """Minuscules, sans accents, µ -> u, virgule décimale -> point, ≤ ≥ -> < >."""
    texte = (texte or "").replace("µ", "u").replace("μ", "u").replace("≤", "<").replace("≥", ">")
    texte = unicodedata.normalize("NFD", texte.lower())
    texte = "".join(c for c in texte if unicodedata.category(c) != "Mn")
    return re.sub(r"(\d),(\d)", r"\1.\2", texte)


def _cherche(motif, texte):
    return re.search(rf"(?<![a-z0-9]){re.escape(motif)}(?![a-z0-9])", texte)


def trouver_analyse(ligne):
    """Analyse citée le plus tôt dans la ligne (à égalité : le synonyme le plus long)."""
    meilleur = None
    for code, analyse in dictionnaire().items():
        for synonyme in analyse["synonymes"]:
            m = _cherche(synonyme, ligne)
            if m and (meilleur is None or (m.start(), -len(synonyme)) < (meilleur[2], -meilleur[3])):
                meilleur = (code, m.end(), m.start(), len(synonyme))
    return (meilleur[0], meilleur[1]) if meilleur else (None, None)


def _arrondi(valeur):
    return round(valeur, 4) if valeur is not None else None


def analyser_ligne(ligne_brute):
    ligne = normaliser(ligne_brute)
    code, fin = trouver_analyse(ligne)
    if code is None:
        return None
    analyse = dictionnaire()[code]
    reste = ligne[fin:]

    bas = haut = None
    m = INTERVALLE.search(reste)
    if m:
        bas, haut = float(m.group(1)), float(m.group(2))
        reste = reste[: m.start()] + " " + reste[m.end():]
    else:
        m = BORNE.search(reste)
        if m:
            if m.group(1) == "<":
                haut = float(m.group(2))
            else:
                bas = float(m.group(2))
            reste = reste[: m.start()] + " " + reste[m.end():]

    unite, facteur = None, 1.0
    for candidate in sorted(analyse["unites"], key=len, reverse=True):
        u = re.search(rf"(?<![a-z]){re.escape(candidate)}(?![a-z0-9])", reste)
        if u:
            unite, facteur = candidate, analyse["unites"][candidate]
            reste = reste[: u.start()] + " " + reste[u.end():]
            break

    m = re.search(NOMBRE, reste)
    if not m:
        return None
    valeur = float(m.group())
    marque = bool(MARQUEUR.search(reste))

    valeur_ref, bas_ref, haut_ref = _arrondi(valeur * facteur), _arrondi(bas * facteur if bas is not None else None), _arrondi(haut * facteur if haut is not None else None)
    return {
        "code": code,
        "name": analyse["libelle"],
        "result": valeur_ref,
        "result_text": "",
        "units": analyse["unite"],
        "lower_limit": bas_ref,
        "upper_limit": haut_ref,
        "normal_range": _intervalle_texte(bas_ref, haut_ref),
        "remarks": "",
        "warning": bool(marque),
        "valeur_source": valeur,
        "unite_source": unite,
        "ligne_source": ligne_brute.strip(),
        "confiance": (int(unite is not None) + int(bas is not None or haut is not None)) / 2,
    }


def _intervalle_texte(bas, haut):
    if bas is not None and haut is not None:
        return f"{bas} – {haut}"
    if haut is not None:
        return f"< {haut}"
    if bas is not None:
        return f"> {bas}"
    return ""


def ressemble_a_un_resultat(ligne_brute):
    ligne = normaliser(ligne_brute)
    return not ENTETE.search(ligne) and bool(INTERVALLE.search(ligne) or BORNE.search(ligne))


def extraire_entete(texte):
    t = normaliser(texte)
    dossier, date = DOSSIER.search(t), DATE_VALIDATION.search(t)
    iso = None
    if date:
        brute = date.group(1)
        iso = brute if "-" in brute else "-".join(reversed(brute.split("/")))
    return {"local_ref": dossier.group(1).upper() if dossier else None, "validation_date": iso}


def texte_du_pdf(data):
    if not data.startswith(b"%PDF"):
        raise SaasError("Le fichier n'est pas un PDF.", 400)
    try:
        import pdfplumber
    except ImportError as exc:  # dépendance à installer sur le serveur
        raise SaasError("Lecture des PDF indisponible : le paquet « pdfplumber » n'est pas installé sur le serveur.", 503) from exc
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)
    except Exception as exc:
        raise SaasError(f"PDF illisible : {exc}", 400) from exc


def extraire_par_regles(texte):
    details, non_reconnues = [], []
    for ligne in texte.splitlines():
        resultat = analyser_ligne(ligne)
        if resultat:
            details.append(resultat)
        elif ressemble_a_un_resultat(ligne):
            non_reconnues.append(ligne.strip())
    return {"methode": "regles", "entete": extraire_entete(texte), "details": details, "a_relire": non_reconnues}


# ---------------------------------------------------------------------
# Lecture par Claude (repli facultatif)
# ---------------------------------------------------------------------

MODELE_IA = "claude-opus-5-5"
SCHEMA_IA = {
    "type": "object",
    "properties": {
        "dossier": {"type": "string"},
        "date_validation": {"type": "string"},
        "analyses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "libelle_imprime": {"type": "string"},
                    "valeur_imprimee": {"type": "string"},
                    "unite_imprimee": {"type": "string"},
                    "normes_imprimees": {"type": "string"},
                    "marque_anormale": {"type": "boolean"},
                },
                "required": ["libelle_imprime", "valeur_imprimee", "unite_imprimee", "normes_imprimees", "marque_anormale"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["dossier", "date_validation", "analyses"],
    "additionalProperties": False,
}
CONSIGNE_IA = """Ce document est un compte rendu d'analyses médicales.
Recopie chaque résultat chiffré, une entrée par analyse, exactement comme il est imprimé :
le libellé, la valeur (avec sa virgule ou son point), l'unité et les valeurs de référence.
N'effectue aucune conversion d'unité et ne corrige rien. Si une information n'est pas imprimée,
laisse le champ vide. marque_anormale vaut true seulement si le document signale la valeur
(astérisque, H, L, flèche, gras, mention « bas » ou « élevé »). Donne le numéro de dossier du
patient et la date de validation (AAAA-MM-JJ) s'ils sont imprimés, sinon une chaîne vide.
Ignore les en-têtes, les adresses et les commentaires sans valeur chiffrée."""


def ia_disponible():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def extraire_par_ia(data):
    if not ia_disponible():
        raise SaasError("Lecture par IA indisponible sur ce serveur (paquet « anthropic » ou clé ANTHROPIC_API_KEY absents).", 503)
    import anthropic

    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=MODELE_IA,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA_IA}},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                                        "data": base64.standard_b64encode(data).decode("ascii")}},
                        {"type": "text", "text": CONSIGNE_IA},
                    ],
                }
            ],
        )
    except anthropic.APIError as exc:
        raise SaasError(f"Lecture par IA impossible : {exc}", 502) from exc
    if response.stop_reason == "refusal":
        raise SaasError("La lecture par IA a été refusée pour ce document.", 422)
    if response.stop_reason == "max_tokens":
        raise SaasError("Document trop long pour la lecture par IA.", 422)
    sortie = json.loads(next(block.text for block in response.content if block.type == "text"))

    details, inconnues = [], []
    for ligne in sortie.get("analyses", []):
        texte = f'{ligne["libelle_imprime"]} {ligne["valeur_imprimee"]} {ligne["unite_imprimee"]} {ligne["normes_imprimees"]}'
        resultat = analyser_ligne(texte)
        if resultat is None:
            inconnues.append(texte.strip())
            continue
        resultat["warning"] = resultat["warning"] or bool(ligne.get("marque_anormale"))
        details.append(resultat)
    entete = {"local_ref": (sortie.get("dossier") or "").strip().upper() or None,
              "validation_date": (sortie.get("date_validation") or "").strip() or None}
    return {"methode": "ia", "entete": entete, "details": details, "a_relire": inconnues}


# ---------------------------------------------------------------------
# Contrôles
# ---------------------------------------------------------------------

def nombres_du_texte(texte):
    return {float(n) for n in re.findall(r"\d+(?:\.\d+)?", normaliser(texte))}


def recalculer_hors_norme(detail):
    valeur, bas, haut = detail.get("result"), detail.get("lower_limit"), detail.get("upper_limit")
    if not isinstance(valeur, (int, float)):
        return bool(detail.get("warning"))
    hors = (bas is not None and valeur < bas) or (haut is not None and valeur > haut)
    return bool(hors or detail.get("warning"))


def controler(details, texte, entete):
    """Liste des anomalies (vide = prêt à publier). Les lignes sont mises à jour (hors norme)."""
    nombres = nombres_du_texte(texte) if texte.strip() else None
    analyses = dictionnaire()
    anomalies = []
    for d in details:
        code, valeur = d.get("code"), d.get("result")
        probleme = None
        if not isinstance(valeur, (int, float)):
            probleme = "valeur non numérique"
        elif code in analyses and not analyses[code]["plausible"][0] <= valeur <= analyses[code]["plausible"][1]:
            probleme = "valeur hors des bornes plausibles"
        elif nombres is not None and d.get("valeur_source") is not None and d["valeur_source"] not in nombres:
            probleme = "valeur absente du texte du PDF"
        elif d.get("confiance", 1) < 1:
            probleme = "unité ou valeurs de référence non reconnues"
        if probleme:
            anomalies.append({"code": code, "name": d.get("name"), "result": valeur, "probleme": probleme})
        d["warning"] = recalculer_hors_norme(d)
    if not details:
        anomalies.append({"code": None, "name": None, "result": None, "probleme": "aucune valeur reconnue"})
    if not entete.get("local_ref"):
        anomalies.append({"code": None, "name": None, "result": None, "probleme": "numéro de dossier introuvable"})
    if not entete.get("validation_date"):
        anomalies.append({"code": None, "name": None, "result": None, "probleme": "date de validation introuvable"})
    return anomalies


def analyser_pdf(data, autoriser_ia=False, forcer_ia=False, local_ref=None):
    """Extraction complète. Renvoie {methode, texte_present, entete, details, a_relire, anomalies, statut}."""
    texte = texte_du_pdf(data)
    resultat = extraire_par_regles(texte) if texte.strip() and not forcer_ia else None
    insuffisant = (
        resultat is None
        or not resultat["details"]
        or resultat["a_relire"]
        or min(d["confiance"] for d in resultat["details"]) < 1
    )
    erreur_ia = None
    if insuffisant and (autoriser_ia or forcer_ia):
        try:
            resultat = extraire_par_ia(data)
        except SaasError as exc:
            if forcer_ia:
                raise
            erreur_ia = exc.message
    if resultat is None:
        resultat = {"methode": "regles", "entete": extraire_entete(texte), "details": [], "a_relire": []}

    if local_ref:
        resultat["entete"]["local_ref"] = local_ref
    anomalies = controler(resultat["details"], texte, resultat["entete"])
    if not texte.strip() and resultat["methode"] != "ia":
        anomalies.insert(0, {"code": None, "name": None, "result": None,
                             "probleme": "PDF sans texte (scanné) : activer la lecture par IA ou saisir les valeurs"})
    resultat.update({
        "texte_present": bool(texte.strip()),
        "anomalies": anomalies,
        "erreur_ia": erreur_ia,
        "statut": STATUS_READY if not anomalies and not resultat["a_relire"] else STATUS_REVIEW,
    })
    return resultat
