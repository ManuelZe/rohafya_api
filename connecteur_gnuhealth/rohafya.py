"""Client de l'API d'ingestion ROHAFYA (/ingest/v1), sans dépendance externe."""
import json
import logging
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

log = logging.getLogger(__name__)

EN_TETE_CLE = "X-ROHAFYA-API-Key"
# Limite fixée par l'API (ingest_batch) : 500 éléments par envoi.
LOT_MAXIMUM = 500


class ErreurRohafya(Exception):
    def __init__(self, message, statut=None):
        Exception.__init__(self, message)
        self.message = message
        self.statut = statut


def transport_http(url_base, cle, timeout=60, ca_file=None):
    """Transport HTTPS réel : (méthode, chemin, corps) → (statut, réponse JSON)."""
    contexte = ssl.create_default_context(cafile=ca_file or None)

    def envoyer(methode, chemin, corps=None):
        donnees = json.dumps(corps).encode("utf-8") if corps is not None else None
        requete = Request(url_base.rstrip("/") + chemin, data=donnees, method=methode)
        requete.add_header(EN_TETE_CLE, cle)
        requete.add_header("Accept", "application/json")
        if donnees is not None:
            requete.add_header("Content-Type", "application/json")
        try:
            with urlopen(requete, timeout=timeout, context=contexte) as reponse:
                return reponse.status, _lire_json(reponse.read())
        except HTTPError as erreur:
            return erreur.code, _lire_json(erreur.read())

    return envoyer


def _lire_json(contenu):
    try:
        return json.loads(contenu.decode("utf-8")) if contenu else {}
    except ValueError:
        return {"message": contenu[:300].decode("utf-8", "replace")}


class ClientRohafya(object):
    def __init__(self, transport, taille_lot=100, essais=3, attente=time.sleep):
        self.transport = transport
        self.taille_lot = max(1, min(int(taille_lot), LOT_MAXIMUM))
        self.essais = max(1, int(essais))
        self.attente = attente

    def _appel(self, methode, chemin, corps=None, statuts_acceptes=(200, 201)):
        """Appel avec nouvelles tentatives sur erreur réseau, 429 et 5xx (attente 2 s, 4 s, 8 s…)."""
        derniere_erreur = None
        for essai in range(self.essais):
            if essai:
                self.attente(2 ** essai)
            try:
                statut, reponse = self.transport(methode, chemin, corps)
            except (URLError, OSError) as erreur:
                derniere_erreur = ErreurRohafya("ROHAFYA injoignable : {}".format(erreur))
                log.warning("%s %s : %s (essai %d/%d)", methode, chemin, erreur, essai + 1, self.essais)
                continue
            if statut in statuts_acceptes:
                return statut, reponse
            message = (reponse or {}).get("message") or "Erreur HTTP {}".format(statut)
            derniere_erreur = ErreurRohafya("{} {} : {}".format(methode, chemin, message), statut)
            if statut != 429 and statut < 500:
                break
            log.warning("%s %s : HTTP %s (essai %d/%d)", methode, chemin, statut, essai + 1, self.essais)
        raise derniere_erreur

    def ping(self):
        """Vérifie la clé ; renvoie l'établissement correspondant."""
        return self._appel("GET", "/ingest/v1/ping")[1]

    def envoyer(self, type_url, elements):
        """Envoie une liste par lots. Chaque lot est enregistré en entier ou pas du tout."""
        totaux = {"created": 0, "updated": 0, "total": 0}
        for debut in range(0, len(elements), self.taille_lot):
            lot = elements[debut:debut + self.taille_lot]
            _, reponse = self._appel("POST", "/ingest/v1/" + type_url, lot)
            for cle in totaux:
                totaux[cle] += int(reponse.get(cle, 0))
        return totaux

    def supprimer(self, type_url, code):
        """Retire un enregistrement. Renvoie False s'il n'avait jamais été envoyé."""
        statut, _ = self._appel("DELETE", "/ingest/v1/{}/{}".format(type_url, quote(code, safe="/")), statuts_acceptes=(200, 404))
        return statut == 200

    def jeton_lien(self, local_ref, email=None, qr=False):
        """Jeton de rattachement (QR code à imprimer sur la facture) pour un dossier patient."""
        corps = {"local_ref": local_ref}
        if email:
            corps["email"] = email
        return self._appel("POST", "/ingest/v1/link-tokens" + ("?qr=png" if qr else ""), corps)[1]
