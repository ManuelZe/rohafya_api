"""Tables du bilan prénuptial (toutes nouvelles, créées par db.create_all())."""
import datetime

from sqlalchemy import JSON

from Rohafya import db
from .catalogue import SEX_ALL


def _now():
    return datetime.datetime.now()


class PremaritalExamType(db.Model):
    """Un examen du catalogue (modifiable par le super-administrateur)."""

    __tablename__ = "premarital_exam_types"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(40), unique=True, nullable=False, index=True)
    sex = db.Column(db.String(3), nullable=False, default=SEX_ALL)  # all, F ou M
    label_fr = db.Column(db.String(200), nullable=False)
    label_en = db.Column(db.String(200), nullable=True)
    purpose_fr = db.Column(db.String(300), nullable=True)
    purpose_en = db.Column(db.String(300), nullable=True)
    reference_fr = db.Column(db.String(500), nullable=True)
    reference_en = db.Column(db.String(500), nullable=True)
    unit = db.Column(db.String(40), nullable=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, default=_now)
    updated_at = db.Column(db.DateTime, nullable=True)


class PremaritalAuthorization(db.Model):
    """Autorisation donnée par un patient à un médecin : consulter et remplir son bilan prénuptial."""

    __tablename__ = "premarital_authorizations"

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True)
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctors.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=_now)
    revoked_at = db.Column(db.DateTime, nullable=True)
    # Dernière notification « bilan mis à jour » envoyée au patient pour ce médecin (évite une notification par examen).
    last_notified_at = db.Column(db.DateTime, nullable=True)

    patient = db.relationship("Patients")
    doctor = db.relationship("Doctors")


class PremaritalExam(db.Model):
    """Résultat d'un examen du catalogue pour un patient : une ligne par patient et par examen."""

    __tablename__ = "premarital_exams"
    __table_args__ = (db.UniqueConstraint("patient_id", "exam_type_id", name="uq_premarital_exam"),)

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True)
    exam_type_id = db.Column(db.Integer, db.ForeignKey("premarital_exam_types.id", ondelete="CASCADE"), nullable=False)
    value = db.Column(db.String(255), nullable=True)
    flag = db.Column(db.String(20), nullable=True)  # normal, abnormal, to_check
    interpretation = db.Column(db.String(1000), nullable=True)
    exam_date = db.Column(db.Date, nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=_now)
    updated_by_doctor_id = db.Column(db.Integer, db.ForeignKey("doctors.id", ondelete="SET NULL"), nullable=True)

    exam_type = db.relationship("PremaritalExamType")
    updated_by = db.relationship("Doctors")


class PremaritalExamHistory(db.Model):
    """Historique complet : chaque modification d'un examen, avec l'état avant et après."""

    __tablename__ = "premarital_exam_history"

    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey("patients.id", ondelete="CASCADE"), nullable=False, index=True)
    exam_type_id = db.Column(db.Integer, db.ForeignKey("premarital_exam_types.id", ondelete="CASCADE"), nullable=False)
    doctor_id = db.Column(db.Integer, db.ForeignKey("doctors.id", ondelete="SET NULL"), nullable=True)
    action = db.Column(db.String(20), nullable=False)  # create, update, clear
    before = db.Column(JSON, nullable=True)
    after = db.Column(JSON, nullable=True)
    changed_at = db.Column(db.DateTime, nullable=False, default=_now)

    exam_type = db.relationship("PremaritalExamType")
    doctor = db.relationship("Doctors")
