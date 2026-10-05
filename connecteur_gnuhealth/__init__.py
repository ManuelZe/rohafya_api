"""Connecteur GNU Health → ROHAFYA.

Installé sur chaque serveur GNU Health, il lit Tryton en local (transaction en lecture seule)
et envoie les patients, résultats et factures à ROHAFYA par l'API d'ingestion
(/ingest/v1/*, en-tête X-ROHAFYA-API-Key). ROHAFYA n'a ainsi plus besoin d'être hébergé
sur le serveur GNU Health, et les données restent consultables même s'il est hors ligne.

Compatible Python 3.6+ (version de trytond 6.0 / GNU Health 4.0), sans dépendance externe
autre que trytond.
"""

__version__ = "1.0.0"
