"""Langue des messages envoyés par l'API : e-mails et notifications système.

Les réponses de l'API restent en français (le front les traduit). En revanche, un e-mail ou une
notification créés par la plateforme sont lus hors de l'interface : ils partent directement dans
la langue du destinataire.

Langue retenue, dans l'ordre :
1. la préférence du compte (saas_user_preferences, choisie avec le sélecteur FR / EN) ;
2. sinon, pour une requête venue du front, la langue de l'interface (en-tête Accept-Language) ;
3. sinon, le français.
"""
import os

from flask import current_app, has_request_context, request
from sqlalchemy import func, select

from Rohafya import db

LOCALES = ("fr", "en")
DEFAULT_LOCALE = "fr"


def _pref_of(user_id):
    from .models import UserPreference

    if not user_id:
        return None
    pref = db.session.execute(select(UserPreference).filter_by(user_id=user_id)).scalar_one_or_none()
    return pref.locale if pref and pref.locale in LOCALES else None


def request_locale():
    """Langue de l'interface qui a fait la requête (le front envoie Accept-Language : fr ou en)."""
    if not has_request_context():
        return None
    best = request.accept_languages.best_match(LOCALES)
    return best if best in LOCALES else None


def user_locale(user_id):
    return _pref_of(user_id) or DEFAULT_LOCALE


def email_locale(email):
    """Langue d'un destinataire connu par son e-mail : celle de son compte, sinon celle de la requête."""
    from Rohafya.accounts.models import User

    if email:
        user_ids = db.session.execute(
            select(User.id).filter(func.lower(User.email) == str(email).strip().lower())
        ).scalars().all()
        for user_id in user_ids:
            pref = _pref_of(user_id)
            if pref:
                return pref
    return request_locale() or DEFAULT_LOCALE


# =====================================================================
# Textes
# =====================================================================

TEXTS = {
    # --- Connexion
    "mail.otp": {"fr": "Votre code de connexion ROHAFYA", "en": "Your ROHAFYA sign-in code"},
    "mail.credentials": {"fr": "Vos identifiants de connexion ROHAFYA", "en": "Your ROHAFYA sign-in details"},
    "mail.account_created": {"fr": "Votre compte ROHAFYA est créé", "en": "Your ROHAFYA account has been created"},
    "mail.admin_invite": {"fr": "Vous administrez {establishment} sur ROHAFYA", "en": "You now manage {establishment} on ROHAFYA"},
    # --- Demandes adressées aux établissements
    "submission.new.prescription": {"fr": "Nouvelle prescription", "en": "New prescription"},
    "submission.new.pre_enregistrement": {"fr": "Nouveau pré-enregistrement", "en": "New pre-registration"},
    "submission.new.requete": {"fr": "Nouvelle requête", "en": "New request"},
    "submission.sent.prescription": {
        "fr": "{author} a envoyé une prescription à {establishment}.",
        "en": "{author} sent a prescription to {establishment}.",
    },
    "submission.sent.pre_enregistrement": {
        "fr": "{author} a envoyé un pré-enregistrement à {establishment}.",
        "en": "{author} sent a pre-registration to {establishment}.",
    },
    "submission.sent.requete": {
        "fr": "{author} a envoyé une requête à {establishment}.",
        "en": "{author} sent a request to {establishment}.",
    },
    "mail.new_submission": {"fr": "ROHAFYA · {title} pour {establishment}", "en": "ROHAFYA · {title} for {establishment}"},
    "kind.prescription": {"fr": "prescription", "en": "prescription"},
    "kind.pre_enregistrement": {"fr": "pré-enregistrement", "en": "pre-registration"},
    "kind.requete": {"fr": "requête", "en": "request"},
    "submission.answer.title": {"fr": "Réponse de {establishment}", "en": "Response from {establishment}"},
    "mail.submission_answer": {"fr": "ROHAFYA · Réponse de {establishment}", "en": "ROHAFYA · Response from {establishment}"},
    "submission.answer.prescription": {
        "fr": "Votre prescription envoyée à {establishment} est {status}.",
        "en": "Your prescription sent to {establishment} is {status}.",
    },
    "submission.answer.pre_enregistrement": {
        "fr": "Votre pré-enregistrement envoyé à {establishment} est {status}.",
        "en": "Your pre-registration sent to {establishment} is {status}.",
    },
    "submission.answer.requete": {
        "fr": "Votre requête envoyée à {establishment} est {status}.",
        "en": "Your request sent to {establishment} is {status}.",
    },
    # Statut accordé au genre de la demande (prescription et requête : féminin).
    "status.f.recue": {"fr": "reçue", "en": "received"},
    "status.f.en_cours": {"fr": "en cours", "en": "in progress"},
    "status.f.traitee": {"fr": "traitée", "en": "processed"},
    "status.f.refusee": {"fr": "refusée", "en": "declined"},
    "status.m.recue": {"fr": "reçu", "en": "received"},
    "status.m.en_cours": {"fr": "en cours", "en": "in progress"},
    "status.m.traitee": {"fr": "traité", "en": "processed"},
    "status.m.refusee": {"fr": "refusé", "en": "declined"},
    # --- Résultats et rattachements
    "result.new.title": {"fr": "Nouveau résultat disponible", "en": "New result available"},
    "result.new.message": {
        "fr": "{establishment} a publié un résultat {kind} ({code}).",
        "en": "{establishment} published a {kind} result ({code}).",
    },
    "result.kind.laboratoire": {"fr": "de laboratoire", "en": "laboratory"},
    "result.kind.imagerie": {"fr": "d'imagerie", "en": "imaging"},
    "result.kind.exploration": {"fr": "d'exploration fonctionnelle", "en": "functional test"},
    "link.active.title": {"fr": "Établissement rattaché", "en": "Facility linked"},
    "link.active.message": {
        "fr": "Votre dossier de {establishment} est désormais accessible dans ROHAFYA.",
        "en": "Your record at {establishment} is now available in ROHAFYA.",
    },
    "share.new.title": {"fr": "Nouveau Résultat Envoyé.", "en": "New shared result"},
    "share.new.message": {
        "fr": "Un nouveau résultat d'examen de type '{exam_type}' avec le code '{exam_code}' a été envoyé par le patient {patient}. Matricule du patient : {patient_id}.",
        "en": "Patient {patient} shared a new {exam_type} result (code {exam_code}). Patient ID: {patient_id}.",
    },
    "share.deleted.title": {"fr": "Résultat Supprimé.", "en": "Shared result removed"},
    "share.deleted.message": {
        "fr": "Le Patient {patient} vient de Supprimer le partage de résultat. Le résultat d'examen de type '{exam_type}' avec le code '{exam_code}' a été supprimé.",
        "en": "Patient {patient} stopped sharing the {exam_type} result with code {exam_code}.",
    },
    # --- Demandes d'essai (super-administrateurs)
    "trial.title": {"fr": "Demande d'essai · {establishment}", "en": "Trial request · {establishment}"},
    "trial.message": {
        "fr": "{contact}, {kind} à {city}. Contact : {phone} · {email}.",
        "en": "{contact}, {kind} in {city}. Contact: {phone} · {email}.",
    },
    # --- Volume et abonnement
    "usage.alert.title": {
        "fr": "Volume de résultats : {percent} % de votre formule",
        "en": "Results volume: {percent}% of your plan",
    },
    "usage.alert.message": {
        "fr": "{establishment} a publié {count} résultats ce mois-ci, pour {quota} compris dans la formule {plan}. La publication continue normalement.",
        "en": "{establishment} has published {count} results this month, out of {quota} included in the {plan} plan. Publishing continues as usual.",
    },
    "mail.usage_alert": {
        "fr": "ROHAFYA · {establishment} : {percent} % du volume de la formule",
        "en": "ROHAFYA · {establishment}: {percent}% of the plan volume",
    },
    "invoice.title": {"fr": "Facture {number} disponible", "en": "Invoice {number} available"},
    "invoice.message": {
        "fr": "La facture {number} de {establishment} ({period}) est disponible : {total} FCFA, à régler avant le {due}.",
        "en": "Invoice {number} for {establishment} ({period}) is available: {total} FCFA, due by {due}.",
    },
    "mail.invoice": {"fr": "ROHAFYA · Facture {number}", "en": "ROHAFYA · Invoice {number}"},
    # --- Sécurité et données personnelles
    "mail.mfa_enabled": {"fr": "Double authentification activée sur votre compte ROHAFYA", "en": "Two-step verification turned on for your ROHAFYA account"},
    "mail.mfa_disabled": {"fr": "Double authentification désactivée sur votre compte ROHAFYA", "en": "Two-step verification turned off for your ROHAFYA account"},
    "deletion.title": {"fr": "Demande de suppression de compte", "en": "Account deletion request"},
    "deletion.message": {
        "fr": "{name} ({email}) demande la suppression de son compte ROHAFYA.",
        "en": "{name} ({email}) asks for their ROHAFYA account to be deleted.",
    },
    "deletion.refused.title": {"fr": "Demande de suppression refusée", "en": "Deletion request declined"},
    "deletion.refused.message": {"fr": "Votre compte n'a pas été supprimé. Motif : {reason}", "en": "Your account was not deleted. Reason: {reason}"},
    "mail.deletion_received": {"fr": "ROHAFYA · Votre demande de suppression est enregistrée", "en": "ROHAFYA · Your deletion request has been recorded"},
}


def text(key, locale=DEFAULT_LOCALE, **params):
    """Texte `key` dans la langue demandée ; une clé inconnue est rendue telle quelle (texte déjà rédigé)."""
    entry = TEXTS.get(key)
    if entry is None:
        return key
    template = entry.get(locale) or entry[DEFAULT_LOCALE]
    try:
        return template.format(**params)
    except (KeyError, IndexError, ValueError):
        return template


def template_for(template, locale):
    """`emails/x.html` → `emails/en/x.html` en anglais, si la version anglaise existe."""
    if locale != "en" or not template.startswith("emails/"):
        return template
    candidate = "emails/en/" + template[len("emails/"):]
    folder = os.path.join(current_app.root_path, current_app.template_folder or "templates")
    return candidate if os.path.exists(os.path.join(folder, candidate)) else template


def format_amount(value, locale=DEFAULT_LOCALE):
    """12 750 (français) ou 12,750 (anglais)."""
    text_value = f"{int(round(value or 0)):,}"
    return text_value.replace(",", " ") if locale == "fr" else text_value
