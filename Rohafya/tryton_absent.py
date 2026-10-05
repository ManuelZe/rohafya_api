"""Déploiement sans GNU Health (ROHAFYA_GNUHEALTH=false).

L'API n'est alors reliée à aucun serveur Tryton : les données des établissements arrivent par
l'API d'ingestion (connecteur GNU Health, API ROHAFYA, FHIR, PDF). Les écrans qui lisaient
GNU Health en direct continuent de répondre, sans données GNU Health :
- toute recherche Tryton renvoie une liste vide ;
- @tryton.transaction() n'ouvre plus de transaction.

Les résultats reçus des établissements, servis par les mêmes routes, restent donc affichés.
Seules search, search_read, search_count et get_preferences sont appelées dans le code.
"""
import logging

log = logging.getLogger(__name__)


class ModeleAbsent(object):
    def __init__(self, nom):
        self.__name__ = nom

    def search(self, *args, **kwargs):
        return []

    def search_read(self, *args, **kwargs):
        return []

    def search_count(self, *args, **kwargs):
        return 0

    def get_preferences(self, *args, **kwargs):
        return {}


class PoolAbsent(object):
    def get(self, nom, type="model"):
        return ModeleAbsent(nom)


def _sans_transaction(*args, **kwargs):
    def decorateur(vue):
        return vue
    return decorateur


def desactiver_tryton(tryton):
    """À appeler avant l'import des blueprints : leurs décorateurs et `tryton.pool.get` au
    chargement des modules utilisent alors les objets ci-dessus."""
    tryton.pool = PoolAbsent()
    tryton.transaction = _sans_transaction
    log.warning("GNU Health désactivé (ROHAFYA_GNUHEALTH=false) : aucune donnée n'est lue en direct dans Tryton.")
