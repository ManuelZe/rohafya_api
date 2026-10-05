"""Passage de ROHAFYA en SaaS : établissements (tenants), liens patient ↔ établissement,
réception des données médicales (API au format ROHAFYA et FHIR), administration.

Toutes les tables de ce paquet sont NOUVELLES : elles sont créées automatiquement par
`db.create_all()` au démarrage de l'API, sans migration des tables existantes.
"""
