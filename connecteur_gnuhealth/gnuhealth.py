"""Lecture de GNU Health (Tryton) en local, dans une transaction en lecture seule.

Ouverture identique à celle de flask_tryton dans l'API : même trytond.conf, même base,
même utilisateur, contexte = préférences de l'utilisateur (société, langue), nécessaire
aux montants calculés des factures.
"""
import getpass
import logging
import os

log = logging.getLogger(__name__)

# Type ROHAFYA → (modèle Tryton, modèle de la demande d'examen, champ de l'ordre dans le résultat).
MODELES_EXAMENS = {
    "laboratoire": ("gnuhealth.lab", "gnuhealth.patient.lab.test", "request_order"),
    "imagerie": ("gnuhealth.imaging.test.result", "gnuhealth.imaging.test.request", "order"),
    "exploration": ("gnuhealth.exp", "gnuhealth.patient.exp.test", "request_order"),
}


def _modifies_depuis(depuis, champ_parent=None, ids_complets=()):
    """Clause « créé ou modifié depuis `depuis` », ou rattaché à un patient à envoyer en entier."""
    clause = ["OR", ("write_date", ">=", depuis), ("create_date", ">=", depuis)]
    if champ_parent and ids_complets:
        clause.append((champ_parent, "in", list(ids_complets)))
    return clause


class ErreurGnuHealth(Exception):
    pass


class SourceGnuHealth(object):
    """Accès en lecture à une base GNU Health. S'utilise avec `with`."""

    def __init__(self, trytond_conf, base, utilisateur_tryton=0, filtre_result_online=True):
        self.trytond_conf = trytond_conf
        self.base = base
        self.utilisateur = int(utilisateur_tryton)
        self.filtre_result_online = filtre_result_online
        self.pool = None
        self._transaction = None
        self._prescripteurs = {}

    # -----------------------------------------------------------------
    # Ouverture / fermeture
    # -----------------------------------------------------------------

    def __enter__(self):
        # Sans accès au fichier, trytond démarrerait avec une configuration vide et échouerait plus loin
        # avec une erreur obscure : on le signale clairement.
        if not os.access(self.trytond_conf, os.R_OK):
            raise ErreurGnuHealth(
                "Fichier {} introuvable ou illisible pour le compte « {} ». Lancez le connecteur avec le "
                "compte système qui fait tourner GNU Health (voir README, étape 2).".format(self.trytond_conf, getpass.getuser())
            )
        try:
            from trytond.config import config
        except ImportError:
            raise ErreurGnuHealth(
                "Module trytond introuvable : lancez le connecteur avec le Python qui fait tourner GNU Health (voir README, étape 2)."
            )

        config.update_etc(self.trytond_conf)

        from trytond.pool import Pool
        from trytond.transaction import Transaction

        self.pool = Pool(self.base)
        with Transaction().start(self.base, self.utilisateur, readonly=True):
            self.pool.init()
            contexte = self.pool.get("res.user").get_preferences(context_only=True)
        # Lecture seule : aucune écriture possible dans GNU Health, la transaction est annulée à la fin.
        self._transaction = Transaction().start(self.base, self.utilisateur, readonly=True, context=contexte)
        self._transaction.__enter__()
        self._verifier_modeles()
        return self

    def __exit__(self, *exc):
        if self._transaction is not None:
            self._transaction.__exit__(*exc)
            self._transaction = None
        return False

    def _modele(self, nom):
        try:
            return self.pool.get(nom)
        except KeyError:
            return None

    def _verifier_modeles(self):
        Party = self._modele("party.party")
        if self.filtre_result_online and "result_online" not in Party._fields:
            raise ErreurGnuHealth(
                "Le champ party.party.result_online n'existe pas dans cette base GNU Health. "
                "Mettez filtre_result_online = false dans la configuration pour envoyer tous les patients."
            )
        for type_examen, (modele, _, _) in MODELES_EXAMENS.items():
            if self._modele(modele) is None:
                log.warning("Modèle %s absent de la base : les résultats « %s » ne seront pas envoyés.", modele, type_examen)

    # -----------------------------------------------------------------
    # Patients
    # -----------------------------------------------------------------

    def _domaine_patients(self):
        domaine = [("is_patient", "=", True), ("federation_account", "!=", None), ("federation_account", "!=", "")]
        if self.filtre_result_online:
            # Case « résultats en ligne » cochée : consentement du patient à la consultation en ligne.
            domaine.append(("result_online", "=", True))
        return domaine

    def matricules_patients(self):
        """{id du party: matricule} de tous les patients dont les données peuvent être envoyées."""
        Party = self.pool.get("party.party")
        lignes = Party.search_read(self._domaine_patients(), fields_names=["federation_account"])
        return {ligne["id"]: ligne["federation_account"] for ligne in lignes}

    def patients(self, depuis=None):
        """Patients (party.party) créés ou modifiés depuis `depuis` (tous si None)."""
        Party = self.pool.get("party.party")
        domaine = self._domaine_patients()
        if depuis is not None:
            domaine.append(_modifies_depuis(depuis))
        return Party.search(domaine, order=[("id", "ASC")])

    # -----------------------------------------------------------------
    # Résultats d'examens
    # -----------------------------------------------------------------

    def examens(self, type_examen, ids_parties, depuis=None, ids_complets=()):
        """Résultats des patients `ids_parties`, créés ou modifiés depuis `depuis`.

        Les patients de `ids_complets` (nouveaux ou qui viennent de donner leur accord)
        reçoivent tout leur historique.
        """
        Modele = self._modele(MODELES_EXAMENS[type_examen][0])
        if Modele is None or not ids_parties:
            return []
        domaine = [("patient.name", "in", list(ids_parties))]
        if depuis is not None:
            domaine.append(_modifies_depuis(depuis, "patient.name", ids_complets))
        return Modele.search(domaine, order=[("id", "ASC")])

    def prescripteur(self, type_examen, examen):
        """Nom du médecin prescripteur (demande d'examen liée à l'ordre du résultat)."""
        _, modele_demande, champ_ordre = MODELES_EXAMENS[type_examen]
        ordre = getattr(examen, champ_ordre, None)
        if not ordre:
            return None
        ordre = getattr(ordre, "id", ordre)
        cle = (modele_demande, ordre)
        if cle not in self._prescripteurs:
            Demande = self._modele(modele_demande)
            nom = None
            if Demande is not None:
                demandes = Demande.search([("request", "=", ordre)], limit=1)
                medecin = demandes[0].service.requestor if demandes and demandes[0].service else None
                if medecin is not None and medecin.name is not None:
                    nom = " ".join(str(m) for m in (medecin.name.name, medecin.name.lastname) if m) or None
            self._prescripteurs[cle] = nom
        return self._prescripteurs[cle]

    # -----------------------------------------------------------------
    # Factures
    # -----------------------------------------------------------------

    def factures(self, ids_parties, depuis=None, ids_complets=()):
        """Factures numérotées des patients `ids_parties`, créées ou modifiées depuis `depuis`.

        Les factures « posted » (non soldées) sont toujours renvoyées : leur reste à payer du jour
        change avec les échéances, sans modification de la facture.
        """
        Invoice = self.pool.get("account.invoice")
        if not ids_parties:
            return []
        domaine = [("party", "in", list(ids_parties)), ("number", "!=", None)]
        if depuis is not None:
            clause = _modifies_depuis(depuis, "party", ids_complets)
            clause.append(("state", "=", "posted"))
            domaine.append(clause)
        return Invoice.search(domaine, order=[("id", "ASC")])

    def numeros_factures(self, ids_parties):
        """{id du party: [(numéro, référence)]} de toutes les factures, pour écarter les avoirs."""
        Invoice = self.pool.get("account.invoice")
        resultat = {}
        if not ids_parties:
            return resultat
        lignes = Invoice.search_read([("party", "in", list(ids_parties))], fields_names=["party", "number", "reference"])
        for ligne in lignes:
            resultat.setdefault(ligne["party"], []).append((ligne["number"], ligne["reference"]))
        return resultat

    def lignes_facture(self, invoice):
        return list(getattr(invoice, "lines", None) or [])
