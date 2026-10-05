"""Synchronisation GNU Health → ROHAFYA.

Principe :
- première exécution (ou --complet) : tout l'historique des patients autorisés est envoyé ;
- ensuite : seulement ce qui a été créé ou modifié depuis la dernière synchronisation réussie,
  avec un chevauchement de quelques minutes (les envois sont idempotents : ROHAFYA met à jour
  un enregistrement déjà reçu au lieu de le dupliquer) ;
- un patient nouveau ou qui vient de donner son accord reçoit tout son historique ;
- la date de dernière synchronisation n'avance que si tout a été envoyé sans erreur :
  après une panne, la synchronisation suivante reprend là où elle en était.
"""
import datetime
import errno
import json
import logging
import os

from . import conversion
from .gnuhealth import MODELES_EXAMENS

log = logging.getLogger(__name__)

FORMAT_DATE = "%Y-%m-%dT%H:%M:%S"
CONVERSIONS = {
    "laboratoire": conversion.laboratoire,
    "imagerie": conversion.imagerie,
    "exploration": conversion.exploration,
}


class Etat(object):
    """Date de la dernière synchronisation réussie, conservée dans un fichier JSON."""

    def __init__(self, chemin):
        self.chemin = chemin
        self.donnees = {}
        if os.path.exists(chemin):
            with open(chemin) as fichier:
                self.donnees = json.load(fichier)

    @property
    def derniere_synchro(self):
        valeur = self.donnees.get("derniere_synchro")
        return datetime.datetime.strptime(valeur, FORMAT_DATE) if valeur else None

    def enregistrer(self, derniere_synchro, bilan):
        self.donnees["derniere_synchro"] = derniere_synchro.strftime(FORMAT_DATE)
        self.donnees["dernier_bilan"] = bilan
        dossier = os.path.dirname(os.path.abspath(self.chemin))
        if not os.path.isdir(dossier):
            os.makedirs(dossier)
        temporaire = self.chemin + ".tmp"
        with open(temporaire, "w") as fichier:
            json.dump(self.donnees, fichier, indent=2, ensure_ascii=False)
        os.rename(temporaire, self.chemin)


class Verrou(object):
    """Empêche deux synchronisations simultanées (cron qui se chevauchent)."""

    def __init__(self, chemin):
        self.chemin = chemin
        self.fichier = None

    def __enter__(self):
        import fcntl

        dossier = os.path.dirname(os.path.abspath(self.chemin))
        if not os.path.isdir(dossier):
            os.makedirs(dossier)
        self.fichier = open(self.chemin, "w")
        try:
            fcntl.flock(self.fichier, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError) as erreur:
            self.fichier.close()
            if erreur.errno in (errno.EAGAIN, errno.EACCES):
                raise RuntimeError("Une synchronisation est déjà en cours ({}).".format(self.chemin))
            raise
        return self

    def __exit__(self, *exc):
        self.fichier.close()
        return False


def synchroniser(source, client, etat=None, complet=False, chevauchement_minutes=10, essai=False, maintenant=None):
    """Envoie à ROHAFYA ce qui a changé dans GNU Health. Renvoie le bilan de l'exécution.

    `essai=True` : lit et convertit tout, sans rien envoyer ni enregistrer (vérification).
    """
    debut = maintenant or datetime.datetime.utcnow()
    depuis = None if (complet or etat is None) else etat.derniere_synchro
    log.info("Synchronisation %s", "complète" if depuis is None else "depuis le {} (UTC)".format(depuis))

    matricules = source.matricules_patients()
    patients = source.patients(depuis)
    # Patients nouveaux ou modifiés (dont ceux qui viennent de cocher « résultats en ligne ») :
    # tout leur historique est renvoyé.
    ids_complets = {party.id for party in patients}
    bilan = {"depuis": depuis.strftime(FORMAT_DATE) if depuis else None, "patients_autorises": len(matricules)}

    def envoyer(type_url, elements):
        if essai:
            bilan[type_url] = {"a_envoyer": len(elements)}
            if elements:
                bilan.setdefault("exemples", {})[type_url] = elements[0]
        else:
            bilan[type_url] = client.envoyer(type_url, elements) if elements else {"created": 0, "updated": 0, "total": 0}
        log.info("%s : %s", type_url, bilan[type_url])

    # 1. Patients
    envoyer("patients", [p for p in (conversion.patient(party) for party in patients) if p["local_ref"]])

    # 2. Résultats d'examens
    for type_examen in MODELES_EXAMENS:
        convertir = CONVERSIONS[type_examen]
        elements = []
        for examen in source.examens(type_examen, matricules, depuis, ids_complets):
            matricule = matricules.get(examen.patient.name.id) if examen.patient and examen.patient.name else None
            if matricule:
                elements.append(convertir(examen, matricule, source.prescripteur(type_examen, examen)))
        envoyer(type_examen, elements)

    # 3. Factures, sans les factures annulées par un avoir ni les avoirs
    factures = source.factures(matricules, depuis, ids_complets)
    numeros = source.numeros_factures({invoice.party.id for invoice in factures})
    elements, a_retirer = [], []
    for invoice in factures:
        annules, avoirs = conversion.factures_annulees(numeros.get(invoice.party.id, []))
        if invoice.number in avoirs:
            # Avoir créé ou modifié depuis la dernière synchronisation : la facture qu'il annule
            # a peut-être déjà été envoyée, on la retire de ROHAFYA.
            a_retirer.append(avoirs[invoice.number])
        if invoice.number in annules:
            continue
        elements.append(conversion.facture(invoice, matricules[invoice.party.id], source.lignes_facture(invoice)))
    envoyer("factures", elements)

    retirees = 0
    if not essai:
        for numero in a_retirer:
            if client.supprimer("factures", numero):
                retirees += 1
    bilan["factures_retirees"] = len(a_retirer) if essai else retirees

    if not essai and etat is not None:
        etat.enregistrer(debut - datetime.timedelta(minutes=chevauchement_minutes), bilan)
    return bilan
