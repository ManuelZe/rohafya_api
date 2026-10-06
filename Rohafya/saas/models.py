"""Tables du module SaaS (toutes nouvelles, créées par db.create_all())."""
import datetime

from sqlalchemy import JSON

from Rohafya import db
from .constants import DEFAULT_SETTINGS, LINK_PENDING


def _now():
    return datetime.datetime.now()


class Tenant(db.Model):
    """Un établissement client de la plateforme (laboratoire, clinique, centre d'imagerie…)."""

    __tablename__ = "saas_tenants"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(80), unique=True, nullable=False, index=True)
    name = db.Column(db.String(200), nullable=False)
    source_type = db.Column(db.String(20), nullable=False, default="api")
    api_key_hash = db.Column(db.String(64), unique=True, nullable=True, index=True)
    api_key_hint = db.Column(db.String(12), nullable=True)
    settings = db.Column(JSON, nullable=False, default=dict)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=_now)
    updated_at = db.Column(db.DateTime, nullable=True)

    def setting(self, key):
        values = self.settings or {}
        return values.get(key, DEFAULT_SETTINGS.get(key))

    def all_settings(self):
        merged = dict(DEFAULT_SETTINGS)
        merged.update(self.settings or {})
        if not merged.get("display_name"):
            merged["display_name"] = self.name
        return merged

    @property
    def display_name(self):
        return self.setting("display_name") or self.name

    def to_dict(self, with_settings=True):
        data = {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "display_name": self.display_name,
            "source_type": self.source_type,
            "is_active": self.is_active,
            "has_api_key": bool(self.api_key_hash),
            "api_key_hint": self.api_key_hint,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if with_settings:
            data["settings"] = self.all_settings()
        return data


class TenantMember(db.Model):
    """Administrateur d'un établissement."""

    __tablename__ = "saas_tenant_members"
    __table_args__ = (db.UniqueConstraint("tenant_id", "user_id", name="uq_saas_member"),)

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("saas_tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role = db.Column(db.String(30), nullable=False, default="admin")
    created_at = db.Column(db.DateTime, default=_now)

    tenant = db.relationship("Tenant")
    user = db.relationship("User")


class TenantPatient(db.Model):
    """Le patient tel que l'établissement le connaît (dossier local), reçu par l'API ou FHIR."""

    __tablename__ = "saas_tenant_patients"
    __table_args__ = (db.UniqueConstraint("tenant_id", "local_ref", name="uq_saas_tenant_patient"),)

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("saas_tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    local_ref = db.Column(db.String(120), nullable=False)
    first_name = db.Column(db.String(120), nullable=True)
    last_name = db.Column(db.String(120), nullable=True)
    email = db.Column(db.String(200), nullable=True, index=True)
    phone = db.Column(db.String(60), nullable=True)
    birth_date = db.Column(db.String(20), nullable=True)
    gender = db.Column(db.String(10), nullable=True)
    source = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime, default=_now)
    updated_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "local_ref": self.local_ref,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "email": self.email,
            "phone": self.phone,
            "birth_date": self.birth_date,
            "gender": self.gender,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class TenantRecord(db.Model):
    """Donnée médicale reçue d'un établissement (résultat d'examen ou facture), au format ROHAFYA."""

    __tablename__ = "saas_tenant_records"
    __table_args__ = (db.UniqueConstraint("tenant_id", "kind", "code", name="uq_saas_record"),)

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("saas_tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    local_ref = db.Column(db.String(120), nullable=False, index=True)
    kind = db.Column(db.String(20), nullable=False, index=True)
    code = db.Column(db.String(120), nullable=False)
    payload = db.Column(JSON, nullable=False, default=dict)
    details = db.Column(JSON, nullable=False, default=list)
    validated_at = db.Column(db.DateTime, nullable=True)
    source = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime, default=_now)
    updated_at = db.Column(db.DateTime, nullable=True)

    tenant = db.relationship("Tenant")

    def to_summary(self):
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "local_ref": self.local_ref,
            "kind": self.kind,
            "code": self.code,
            "title": (self.payload or {}).get("test")
            or (self.payload or {}).get("requested_test")
            or (self.payload or {}).get("invoice_number")
            or self.code,
            "validated_at": self.validated_at,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "details_count": len(self.details or []),
        }


class PatientLink(db.Model):
    """Lien vérifié entre un profil patient ROHAFYA et son dossier local dans un établissement."""

    __tablename__ = "saas_patient_links"
    __table_args__ = (db.UniqueConstraint("tenant_id", "local_ref", name="uq_saas_patient_link"),)

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("saas_tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    local_ref = db.Column(db.String(120), nullable=False)
    status = db.Column(db.String(20), nullable=False, default=LINK_PENDING)
    method = db.Column(db.String(20), nullable=True)
    verified_at = db.Column(db.DateTime, nullable=True)
    verified_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, default=_now)
    updated_at = db.Column(db.DateTime, nullable=True)

    tenant = db.relationship("Tenant")
    patient = db.relationship("Patients")

    def to_dict(self):
        patient = self.patient
        return {
            "id": self.id,
            "patient_id": self.patient_id,
            "tenant_id": self.tenant_id,
            "establishment": self.tenant.display_name if self.tenant else None,
            "local_ref": self.local_ref,
            "status": self.status,
            "method": self.method,
            "verified_at": self.verified_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "patient_name": f"{patient.PatientName or ''} {patient.PatientLastname or ''}".strip() if patient else None,
            "patient_email": patient.PatientEmail if patient else None,
            "patient_federation_id": patient.PatientFederationID if patient else None,
        }


class PractitionerLink(db.Model):
    """Rattachement d'un médecin à un établissement."""

    __tablename__ = "saas_practitioner_links"
    __table_args__ = (db.UniqueConstraint("tenant_id", "doctor_id", name="uq_saas_practitioner_link"),)

    id = db.Column(db.Integer, primary_key=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("saas_tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    local_ref = db.Column(db.String(120), nullable=True)
    created_at = db.Column(db.DateTime, default=_now)

    tenant = db.relationship("Tenant")
    doctor = db.relationship("Doctors")

    def to_dict(self):
        doctor = self.doctor
        return {
            "id": self.id,
            "doctor_id": self.doctor_id,
            "tenant_id": self.tenant_id,
            "local_ref": self.local_ref,
            "created_at": self.created_at,
            "name": f"{doctor.DoctorName or ''} {doctor.DoctorLastname or ''}".strip() if doctor else None,
            "email": doctor.DoctorEmail if doctor else None,
            "matricule": doctor.DoctorFederationID if doctor else None,
            "speciality": doctor.Speciality if doctor else None,
            "is_confirmed": bool(doctor.doctor_is_confirmed) if doctor else False,
        }


class LinkToken(db.Model):
    """Jeton de rattachement caché derrière le QR code imprimé sur la facture."""

    __tablename__ = "saas_link_tokens"

    id = db.Column(db.Integer, primary_key=True)
    token_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    short_hash = db.Column(db.String(64), unique=True, nullable=False, index=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("saas_tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    local_ref = db.Column(db.String(120), nullable=False, index=True)
    email_hint = db.Column(db.String(200), nullable=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)
    used_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    revoked_at = db.Column(db.DateTime, nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, default=_now)

    tenant = db.relationship("Tenant")

    @property
    def status(self):
        if self.revoked_at:
            return "revoked"
        if self.used_at:
            return "used"
        if self.expires_at and self.expires_at < _now():
            return "expired"
        return "active"

    def to_dict(self):
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "local_ref": self.local_ref,
            "email_hint": self.email_hint,
            "expires_at": self.expires_at,
            "used_at": self.used_at,
            "revoked_at": self.revoked_at,
            "created_at": self.created_at,
            "status": self.status,
        }


class EmailOtp(db.Model):
    """Code à usage unique envoyé par e-mail (connexion / création de compte)."""

    __tablename__ = "saas_email_otps"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200), nullable=False, index=True)
    code_hash = db.Column(db.String(64), nullable=False)
    purpose = db.Column(db.String(20), nullable=False, default="login")
    attempts = db.Column(db.Integer, nullable=False, default=0)
    expires_at = db.Column(db.DateTime, nullable=False)
    used_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=_now)


class SaasAttempt(db.Model):
    """Trace des tentatives sensibles (saisie de code court) pour limiter les essais."""

    __tablename__ = "saas_attempts"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=True, index=True)
    action = db.Column(db.String(40), nullable=False)
    success = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=_now, index=True)


class AuditLog(db.Model):
    """Journal d'audit : qui a fait quoi, sur quel établissement, et quand."""

    __tablename__ = "saas_audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("saas_tenants.id", ondelete="SET NULL"), nullable=True, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    action = db.Column(db.String(60), nullable=False)
    target = db.Column(db.String(200), nullable=True)
    details = db.Column(JSON, nullable=True)
    created_at = db.Column(db.DateTime, default=_now, index=True)

    tenant = db.relationship("Tenant")
    user = db.relationship("User")

    def to_dict(self):
        user = self.user
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "establishment": self.tenant.display_name if self.tenant else None,
            "user_id": self.user_id,
            "user_name": f"{user.first_name or ''} {user.last_name or ''}".strip() if user else None,
            "user_email": user.email if user else None,
            "action": self.action,
            "target": self.target,
            "details": self.details,
            "created_at": self.created_at,
        }


class PdfImport(db.Model):
    """Compte rendu PDF déposé par un établissement : extraction, relecture, publication."""

    __tablename__ = "saas_pdf_imports"

    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("saas_tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = db.Column(db.String(255), nullable=True)
    file_size = db.Column(db.Integer, nullable=True)
    file_data = db.Column(db.LargeBinary, nullable=False)
    status = db.Column(db.String(20), nullable=False, default="a_relire", index=True)
    method = db.Column(db.String(20), nullable=True)
    local_ref = db.Column(db.String(120), nullable=True, index=True)
    exam_code = db.Column(db.String(120), nullable=True)
    title = db.Column(db.String(200), nullable=True)
    validation_date = db.Column(db.String(20), nullable=True)
    extraction = db.Column(JSON, nullable=False, default=dict)
    source = db.Column(db.String(20), nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime, default=_now, index=True)
    updated_at = db.Column(db.DateTime, nullable=True)
    published_at = db.Column(db.DateTime, nullable=True)
    published_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    record_id = db.Column(db.Integer, db.ForeignKey("saas_tenant_records.id", ondelete="SET NULL"), nullable=True)

    def to_dict(self, full=False):
        extraction = self.extraction or {}
        data = {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "filename": self.filename,
            "file_size": self.file_size,
            "status": self.status,
            "method": self.method,
            "local_ref": self.local_ref,
            "exam_code": self.exam_code,
            "title": self.title,
            "validation_date": self.validation_date,
            "source": self.source,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "published_at": self.published_at,
            "values_count": len(extraction.get("details") or []),
            "issues_count": len(extraction.get("anomalies") or []) + len(extraction.get("a_relire") or []),
        }
        if full:
            data["extraction"] = extraction
        return data


class Submission(db.Model):
    """Adressage d'une prescription, d'un pré-enregistrement ou d'une requête à un établissement.

    Le contenu reste dans sa table d'origine (prescriptions, save_patients, requests) ; cette table
    porte l'établissement destinataire, l'auteur, le statut et la réponse de l'établissement.
    """

    __tablename__ = "saas_submissions"
    __table_args__ = (db.UniqueConstraint("kind", "item_id", name="uq_saas_submission_item"),)

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(30), nullable=False, index=True)
    item_id = db.Column(db.Integer, nullable=False)
    tenant_id = db.Column(db.Integer, db.ForeignKey("saas_tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    author_role = db.Column(db.String(20), nullable=False, default="patient")
    # Patient concerné, quand l'auteur est un médecin (prescription pour un patient).
    patient_name = db.Column(db.String(200), nullable=True)
    status = db.Column(db.String(20), nullable=False, default="recue", index=True)
    response = db.Column(db.Text, nullable=True)
    quote_amount = db.Column(db.Float, nullable=True)
    responded_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    responded_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=_now)
    updated_at = db.Column(db.DateTime, nullable=True)

    tenant = db.relationship("Tenant")
    author = db.relationship("User", foreign_keys=[author_id])

    def to_dict(self):
        from .constants import SUBMISSION_STATUS_LABELS

        return {
            "id": self.id,
            "kind": self.kind,
            "tenant_id": self.tenant_id,
            "establishment": self.tenant.display_name if self.tenant else None,
            "author_role": self.author_role,
            "patient_name": self.patient_name,
            "status": self.status,
            "status_label": SUBMISSION_STATUS_LABELS.get(self.status, self.status),
            "response": self.response,
            "quote_amount": self.quote_amount,
            "responded_at": self.responded_at,
            "created_at": self.created_at,
        }
