"""Ligne de commande du connecteur.

    python -m connecteur_gnuhealth -c /etc/rohafya/connecteur.ini verifier
    python -m connecteur_gnuhealth -c /etc/rohafya/connecteur.ini synchro [--complet] [--essai]
    python -m connecteur_gnuhealth -c /etc/rohafya/connecteur.ini lien <matricule> [--qr fichier.png]
"""
import argparse
import base64
import getpass
import json
import logging
import sys

from . import __version__
from .config import Configuration, ErreurConfiguration
from .gnuhealth import ErreurGnuHealth, SourceGnuHealth
from .rohafya import ClientRohafya, ErreurRohafya, transport_http
from .synchro import Etat, Verrou, synchroniser

log = logging.getLogger("connecteur_gnuhealth")


def _journalisation(fichier=None, bavard=False):
    gestionnaires = [logging.StreamHandler()]
    if fichier:
        gestionnaires.append(logging.FileHandler(fichier, encoding="utf-8"))
    logging.basicConfig(
        level=logging.DEBUG if bavard else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s : %(message)s",
        handlers=gestionnaires,
    )


def _client(config):
    return ClientRohafya(transport_http(config.url, config.cle_api, config.timeout, config.ca_file), config.taille_lot)


def _source(config):
    return SourceGnuHealth(config.trytond_conf, config.base, config.utilisateur_tryton, config.filtre_result_online)


def commande_verifier(config, args):
    etablissement = _client(config).ping()
    print("ROHAFYA : clé valide pour « {} » (type {}).".format(etablissement.get("establishment"), etablissement.get("source_type")))
    if etablissement.get("source_type") == "gnuhealth":
        print("  Attention : un établissement de type « gnuhealth » est lu en direct par l'API ;"
              " les données envoyées ne seront pas affichées. Utilisez un établissement de type « api ».")
    print("Compte système : {} ; Python : {}".format(getpass.getuser(), sys.executable))
    with _source(config) as source:
        print("GNU Health : base « {} » ouverte, {} patient(s) autorisé(s).".format(config.base, len(source.matricules_patients())))
    etat = Etat(config.fichier_etat)
    print("Dernière synchronisation réussie : {}".format(etat.derniere_synchro or "jamais"))
    return 0


def commande_synchro(config, args):
    with Verrou(config.fichier_etat + ".lock"):
        etat = Etat(config.fichier_etat)
        with _source(config) as source:
            bilan = synchroniser(
                source,
                _client(config),
                etat,
                complet=args.complet,
                chevauchement_minutes=config.chevauchement_minutes,
                essai=args.essai,
            )
    if args.essai:
        print(json.dumps(bilan, indent=2, ensure_ascii=False))
    return 0


def commande_lien(config, args):
    reponse = _client(config).jeton_lien(args.matricule, email=args.email, qr=bool(args.qr))
    if reponse.get("already_linked"):
        print("Le dossier {} est déjà rattaché à un compte ROHAFYA.".format(args.matricule))
        return 0
    print("Lien : {}".format(reponse["url"]))
    print("Code : {} (valable jusqu'au {})".format(reponse["short_code"], reponse["expires_at"]))
    if args.qr:
        with open(args.qr, "wb") as fichier:
            fichier.write(base64.b64decode(reponse["qr_png"]))
        print("QR code enregistré dans {}".format(args.qr))
    return 0


def main(argv=None):
    parseur = argparse.ArgumentParser(prog="connecteur_gnuhealth", description="Connecteur GNU Health → ROHAFYA")
    parseur.add_argument("-c", "--config", default="/etc/rohafya/connecteur.ini", help="fichier de configuration")
    parseur.add_argument("-v", "--bavard", action="store_true", help="journal détaillé")
    parseur.add_argument("--version", action="version", version=__version__)
    sous = parseur.add_subparsers(dest="commande")

    sous.add_parser("verifier", help="vérifie la clé ROHAFYA et l'accès à GNU Health")

    synchro = sous.add_parser("synchro", help="envoie à ROHAFYA ce qui a changé")
    synchro.add_argument("--complet", action="store_true", help="renvoie tout l'historique")
    synchro.add_argument("--essai", action="store_true", help="lit et convertit sans rien envoyer")

    lien = sous.add_parser("lien", help="crée le QR code de rattachement d'un patient")
    lien.add_argument("matricule", help="matricule GNU Health (federation_account)")
    lien.add_argument("--email", help="e-mail du patient (rattachement immédiat s'il correspond)")
    lien.add_argument("--qr", metavar="FICHIER.png", help="enregistre aussi le QR code en PNG")

    args = parseur.parse_args(argv)
    if not args.commande:
        parseur.print_help()
        return 2

    try:
        config = Configuration(args.config)
        _journalisation(config.journal, args.bavard)
        commande = {"verifier": commande_verifier, "synchro": commande_synchro, "lien": commande_lien}[args.commande]
        return commande(config, args)
    except (ErreurConfiguration, ErreurGnuHealth, ErreurRohafya, RuntimeError) as erreur:
        logging.basicConfig()
        log.error("%s", erreur)
        return 1


if __name__ == "__main__":
    sys.exit(main())
