"""Constantes partagées par le module SaaS."""
import os

# Rôles (table `roles`). « Admin » est le rôle historique d'administration globale :
# il est traité comme un super-administrateur.
ROLE_SUPER_ADMIN = "SuperAdmin"
ROLE_LEGACY_ADMIN = "Admin"
ROLE_TENANT_ADMIN = "EstablishmentAdmin"
ROLE_PATIENT = "Patient"
ROLE_DOCTOR = "Doctor"

# Permissions attribuées par défaut (flask saas init) : chaque rôle reçoit toutes les permissions de sa famille.
# Sans elles, un compte patient ou médecin reçoit « Permission denied » sur toutes ses requêtes.
DEFAULT_ROLE_PERMISSION_PREFIXES = {ROLE_PATIENT: "patients.", ROLE_DOCTOR: "doctors."}

SUPER_ADMIN_ROLES = {ROLE_SUPER_ADMIN, ROLE_LEGACY_ADMIN}

# Origine des données d'un établissement.
SOURCE_GNUHEALTH = "gnuhealth"   # lecture directe dans GNU Health (Tryton), historique PDMD Santé
SOURCE_API = "api"               # envoi par l'API ROHAFYA, au format des données de la démo
SOURCE_FHIR = "fhir"             # envoi au standard HL7 FHIR R4
SOURCE_PDF = "pdf"               # dépôt de comptes rendus PDF (fichier ou scan) relus sur la plateforme
SOURCES = (SOURCE_GNUHEALTH, SOURCE_API, SOURCE_FHIR, SOURCE_PDF)

# Fronts autorisés (CORS) et vers lesquels pointent les QR codes et les e-mails.
# Surchargeable par ROHAFYA_FRONT_ORIGINS (liste séparée par des virgules).
DEFAULT_FRONT_ORIGINS = (
    "https://rohafya.com",
    "https://preprod.rohafya.com",
    "http://localhost:4000",
    "http://localhost:4200",
)


def front_origins():
    raw = os.environ.get("ROHAFYA_FRONT_ORIGINS")
    origins = raw.split(",") if raw else DEFAULT_FRONT_ORIGINS
    return [origin.strip().rstrip("/") for origin in origins if origin.strip()]

# Types de données reçues.
KIND_LAB = "laboratoire"
KIND_IMAGING = "imagerie"
KIND_EXPLORATION = "exploration"
KIND_INVOICE = "facture"
RECORD_KINDS = (KIND_LAB, KIND_IMAGING, KIND_EXPLORATION, KIND_INVOICE)

# Correspondance avec les types d'examen utilisés par le partage de résultats (Send_Results).
EXAM_TYPE_TO_KIND = {
    "Laboratoire": KIND_LAB,
    "Imagerie": KIND_IMAGING,
    "Exploration": KIND_EXPLORATION,
}

# Champ qui porte le code unique d'un enregistrement, par type (même format que la démo).
CODE_FIELD = {
    KIND_LAB: "name",
    KIND_IMAGING: "number",
    KIND_EXPLORATION: "name",
    KIND_INVOICE: "reference",
}

# Demandes adressées à un établissement par un patient, un médecin ou un visiteur :
# prescriptions, pré-enregistrements et requêtes (table saas_submissions).
SUBMISSION_PRESCRIPTION = "prescription"
SUBMISSION_PRE_REGISTRATION = "pre_enregistrement"
SUBMISSION_REQUEST = "requete"
SUBMISSION_KINDS = (SUBMISSION_PRESCRIPTION, SUBMISSION_PRE_REGISTRATION, SUBMISSION_REQUEST)
SUBMISSION_KIND_LABELS = {
    SUBMISSION_PRESCRIPTION: "prescription",
    SUBMISSION_PRE_REGISTRATION: "pré-enregistrement",
    SUBMISSION_REQUEST: "requête",
}

SUBMISSION_RECEIVED = "recue"
SUBMISSION_IN_PROGRESS = "en_cours"
SUBMISSION_DONE = "traitee"
SUBMISSION_REFUSED = "refusee"
SUBMISSION_STATUSES = (SUBMISSION_RECEIVED, SUBMISSION_IN_PROGRESS, SUBMISSION_DONE, SUBMISSION_REFUSED)
SUBMISSION_STATUS_LABELS = {
    SUBMISSION_RECEIVED: "Reçue",
    SUBMISSION_IN_PROGRESS: "En cours",
    SUBMISSION_DONE: "Traitée",
    SUBMISSION_REFUSED: "Refusée",
}

# Auteur d'une demande.
AUTHOR_PATIENT = "patient"
AUTHOR_DOCTOR = "doctor"
AUTHOR_ANONYMOUS = "anonyme"
AUTHOR_ROLES = (AUTHOR_PATIENT, AUTHOR_DOCTOR, AUTHOR_ANONYMOUS)

# Liens patient ↔ établissement.
LINK_PENDING = "pending"
LINK_ACTIVE = "active"
LINK_REVOKED = "revoked"
LINK_STATUSES = (LINK_PENDING, LINK_ACTIVE, LINK_REVOKED)

LINK_METHOD_QR = "qr"
LINK_METHOD_CODE = "code"
LINK_METHOD_ADMIN = "admin"
LINK_METHOD_LEGACY = "legacy"

# Identifiant global attribué aux patients créés par e-mail (hors GNU Health).
ROHAFYA_ID_PREFIX = "ROHAFYA-"
# Préfixe des identifiants attribués du temps d'EDEN, toujours reconnus (pas de migration des patients existants).
LEGACY_ID_PREFIX = "EDEN-"
PATIENT_ID_PREFIXES = (ROHAFYA_ID_PREFIX, LEGACY_ID_PREFIX)

# Paramètres par défaut d'un établissement (modifiables dans son back-office).
DEFAULT_SETTINGS = {
    "display_name": "",
    "result_access_days": 90,
    "block_unpaid_results": True,
    "commissions_enabled": False,
    "link_token_days": 30,
    # Couleur de la console de l'établissement (vert du logo ROHAFYA par défaut).
    "primary_color": "#047857",
    "contact_email": "",
    "contact_phone": "",
    # Lecture des comptes rendus PDF par IA (Claude) : désactivée tant que l'établissement n'a pas
    # donné son accord pour l'envoi de ses PDF à un service externe. Réglée par le super-administrateur.
    "pdf_ai_enabled": False,
    # Quota d'imports PDF : nombre de fichiers par période de N jours (réglé par le super-administrateur).
    "pdf_quota_files": 10,
    "pdf_quota_days": 1,
}

# Bornes des réglages numériques fixés par le super-administrateur.
PDF_QUOTA_LIMITS = {"pdf_quota_files": (1, 10000), "pdf_quota_days": (1, 365)}

# Paramètres qu'un administrateur d'établissement peut modifier lui-même.
TENANT_EDITABLE_SETTINGS = (
    "display_name",
    "result_access_days",
    "block_unpaid_results",
    "link_token_days",
    "primary_color",
    "contact_email",
    "contact_phone",
)

# Code à usage unique envoyé par e-mail.
OTP_LENGTH = 6
OTP_TTL_MINUTES = 10
OTP_MAX_ATTEMPTS = 5
OTP_MAX_REQUESTS_PER_HOUR = 5

# Code court imprimé sous le QR code (sans 0/O/1/I pour éviter les confusions).
SHORT_CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
SHORT_CODE_LENGTH = 7
REDEEM_MAX_ATTEMPTS_PER_HOUR = 10

# Clé d'API des établissements (en-tête HTTP).
API_KEY_HEADER = "X-ROHAFYA-API-Key"
# Ancien en-tête, accepté pendant la transition.
LEGACY_API_KEY_HEADER = "X-EDEN-API-Key"
API_KEY_PREFIX = "rohafya_"

# Identifiant de l'établissement GNU Health historique (créé au premier besoin).
DEFAULT_GNUHEALTH_SLUG = "pdmd-sante"
DEFAULT_GNUHEALTH_NAME = "PDMD Santé"

# =====================================================================
# Abonnements des établissements (grille du site de présentation et du document de vente)
# =====================================================================
# Prix en FCFA. `quota` : résultats publiés par mois compris dans la formule (None : sur devis).
PLAN_ESSENTIEL = "essentiel"
PLAN_STANDARD = "standard"
PLAN_AVANCE = "avance"
PLAN_RESEAU = "reseau"
PLANS = {
    PLAN_ESSENTIEL: {"label": "Essentiel", "quota": 500, "monthly": 25_000, "setup": 75_000},
    PLAN_STANDARD: {"label": "Standard", "quota": 2_000, "monthly": 50_000, "setup": 150_000},
    PLAN_AVANCE: {"label": "Avancé", "quota": 6_000, "monthly": 100_000, "setup": 300_000},
    PLAN_RESEAU: {"label": "Réseau ou hôpital", "quota": None, "monthly": 200_000, "setup": 500_000},
}
PLAN_ORDER = (PLAN_ESSENTIEL, PLAN_STANDARD, PLAN_AVANCE, PLAN_RESEAU)

SUB_TRIAL = "essai"
SUB_ACTIVE = "actif"
SUB_SUSPENDED = "suspendu"
SUB_ENDED = "resilie"
SUB_STATUSES = (SUB_TRIAL, SUB_ACTIVE, SUB_SUSPENDED, SUB_ENDED)
TRIAL_DAYS = 60

# Paiement d'avance (« terme à échoir ») : nombre de mois couverts et nombre de mois payés.
# Annuel : dix mois payés pour douze (deux mois offerts).
BILLING_CYCLES = {"mensuel": (1, 1), "trimestriel": (3, 3), "annuel": (12, 10)}
INVOICE_DUE_DAYS = 15
INVOICE_ISSUED = "emise"
INVOICE_PAID = "payee"
INVOICE_CANCELLED = "annulee"
INVOICE_STATUSES = (INVOICE_ISSUED, INVOICE_PAID, INVOICE_CANCELLED)

# Alertes de volume (pourcentage de la formule atteint dans le mois). La publication n'est jamais bloquée.
USAGE_ALERT_THRESHOLDS = (80, 100)
# Changement de formule : constaté sur trois mois consécutifs, appliqué au trimestre suivant.
PLAN_CHANGE_MONTHS = 3
