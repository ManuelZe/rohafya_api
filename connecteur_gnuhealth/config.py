"""Lecture du fichier de configuration (format INI, voir config.exemple.ini)."""
import os

try:
    from configparser import ConfigParser
except ImportError:  # pragma: no cover
    from ConfigParser import ConfigParser


class ErreurConfiguration(Exception):
    pass


VRAI = ("1", "true", "oui", "yes", "on")


class Configuration(object):
    def __init__(self, chemin):
        if not os.path.exists(chemin):
            raise ErreurConfiguration("Fichier de configuration introuvable : {}".format(chemin))
        parseur = ConfigParser()
        parseur.read(chemin, encoding="utf-8")

        def lire(section, cle, defaut=None, obligatoire=False):
            valeur = parseur.get(section, cle, fallback="").strip() if parseur.has_section(section) else ""
            if not valeur and obligatoire:
                raise ErreurConfiguration("Paramètre manquant : [{}] {}".format(section, cle))
            return valeur or defaut

        self.url = lire("rohafya", "url", obligatoire=True)
        # La clé peut venir de l'environnement pour ne pas l'écrire dans le fichier.
        self.cle_api = os.environ.get("ROHAFYA_CLE_API") or lire("rohafya", "cle_api", obligatoire=True)
        self.ca_file = lire("rohafya", "ca_file")
        self.timeout = int(lire("rohafya", "timeout", "60"))
        self.taille_lot = int(lire("rohafya", "taille_lot", "100"))

        self.trytond_conf = lire("gnuhealth", "trytond_conf", obligatoire=True)
        self.base = lire("gnuhealth", "base", obligatoire=True)
        self.utilisateur_tryton = int(lire("gnuhealth", "utilisateur_tryton", "0"))
        self.filtre_result_online = lire("gnuhealth", "filtre_result_online", "true").lower() in VRAI

        self.fichier_etat = lire("connecteur", "fichier_etat", "/var/lib/rohafya-connecteur/etat.json")
        self.chevauchement_minutes = int(lire("connecteur", "chevauchement_minutes", "10"))
        self.journal = lire("connecteur", "journal")

        if not self.url.startswith("https://") and not self.url.startswith("http://localhost"):
            raise ErreurConfiguration("L'URL de ROHAFYA doit être en https:// (données de santé).")
