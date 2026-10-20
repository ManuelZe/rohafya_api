"""Bilan prénuptial : seul un médecin autorisé modifie, le patient lit, tout est historisé.

SQLite en mémoire. Lancement, depuis la racine du dépôt :
    Rohafya/envDoc/bin/python Rohafya/tests/test_prenuptial.py
"""
import datetime
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flask import Flask
from flask_jwt_extended import create_access_token
from sqlalchemy import JSON, null, select

import Rohafya
from Rohafya import db, jwt, login_manager

app = Flask("Rohafya", root_path=os.path.dirname(Rohafya.__file__))
app.config.update(
    SQLALCHEMY_DATABASE_URI="sqlite://",
    SECRET_KEY="test-secret-test-secret-test-secret",
    JWT_SECRET_KEY="test-jwt-test-jwt-test-jwt-test-jwt",
    JWT_TOKEN_LOCATION=["headers"],
    USER_APP_NAME="ROHAFYA",
    USER_ENABLE_EMAIL=True,
    USER_ENABLE_USERNAME=True,
    USER_EMAIL_SENDER_EMAIL="noreply@test",
    MAIL_SUPPRESS_SEND=True,
    TESTING=True,
    USER_LOGIN_URL="/user/login",
)
db.init_app(app)
jwt.init_app(app)
login_manager.init_app(app)

from Rohafya.accounts.models import Doctors, Notifications, User, UserActivity  # noqa: E402
from Rohafya.custom.customization import CustomUserManager  # noqa: E402

CustomUserManager(app, db, User)


@jwt.user_identity_loader
def _identity(user):
    return user


@jwt.user_lookup_loader
def _lookup(_header, data):
    return db.session.get(User, int(data["sub"]))


from Rohafya import tryton  # noqa: E402
from Rohafya.tryton_absent import desactiver_tryton  # noqa: E402

desactiver_tryton(tryton)

from Rohafya.premarital import services as premarital_services  # noqa: E402
from Rohafya.premarital.catalogue import DEFAULT_EXAMS  # noqa: E402
from Rohafya.premarital.models import PremaritalExam, PremaritalExamHistory  # noqa: E402
from Rohafya.premarital.routes import premarital  # noqa: E402
from Rohafya.saas import privacy, services  # noqa: E402

app.register_blueprint(premarital)
UserActivity.__table__.c.route.type = JSON()
services.send_mail = lambda *args, **kwargs: True

ok_count = 0


def check(condition, label):
    global ok_count
    if not condition:
        raise AssertionError(label)
    ok_count += 1
    print("  ✔", label)


with app.app_context():
    db.create_all()
    premarital_services.ensure_catalogue()
    premarital_services.ensure_catalogue()  # idempotent
    client = app.test_client()

    def auth(user):
        return {"Authorization": "Bearer " + create_access_token(identity=str(user.id))}

    admin = services.create_user("root@rohafya.test", "Root", "Admin", "SuperAdmin")
    patient_user = services.create_user("aicha@example.com", "Aïcha", "Ngono", "Patient")
    patient = services.ensure_patient_profile(patient_user)
    patient.PatientName, patient.PatientLastname, patient.PatientGender = "Aïcha", "Ngono", "F"
    other_user = services.create_user("paul@example.com", "Paul", "Mbarga", "Patient")
    other_patient = services.ensure_patient_profile(other_user)
    doctors = []
    for i in range(2):
        doctor_user = services.create_user(f"doc{i}@example.com", "Doc", str(i), "Doctor")
        doctor = Doctors(
            user_id=doctor_user.id, DoctorEmail=doctor_user.email, DoctorName="Jean", DoctorLastname=f"Tchinda{i}",
            DoctorFederationID=f"MED-00{i}", DoctorPhone=f"69000000{i}", DoctorPhone2=null(), doctor_is_confirmed=True,
        )
        db.session.add(doctor)
        doctors.append((doctor_user, doctor))
    db.session.commit()
    (doc_user, doctor), (intruder_user, intruder) = doctors
    base = f"/premarital/doctor/patients/{patient.id}"

    print("1. Catalogue initial")
    check(len(client.get("/premarital/catalogue", headers=auth(admin)).get_json()) == len(DEFAULT_EXAMS), "catalogue de la fiche inséré une fois")
    me = client.get("/premarital/me", headers=auth(patient_user)).get_json()
    sexes = {exam["sex"] for exam in me["exams"]}
    check(sexes == {"all", "F"}, "patiente : examens communs et féminins seulement")
    check(all(exam["updated_at"] is None for exam in me["exams"]), "aucun examen renseigné au départ")
    other = client.get("/premarital/me", headers=auth(other_user)).get_json()
    check({exam["sex"] for exam in other["exams"]} == {"all", "F", "M"}, "sexe non renseigné : toutes les sections")

    print("2. Autorisations")
    check(client.get(base, headers=auth(doc_user)).status_code == 403, "médecin non autorisé : accès refusé")
    r = client.post("/premarital/me/doctors", json={"matricule": "med-000"}, headers=auth(patient_user))
    check(r.status_code == 201 and r.get_json()["doctor"]["doctor"]["id"] == doctor.id, "autorisation par matricule (casse ignorée)")
    check(client.post("/premarital/me/doctors", json={"matricule": "MED-000"}, headers=auth(patient_user)).status_code == 409, "double autorisation refusée")
    check(client.post("/premarital/me/doctors", json={"matricule": "INCONNU"}, headers=auth(patient_user)).status_code == 404, "matricule inconnu")
    note = db.session.execute(select(Notifications).filter_by(user_id=doc_user.id)).scalars().first()
    check(note is not None and "Aïcha" in note.message, "le médecin est notifié")
    patients = client.get("/premarital/doctor/patients", headers=auth(doc_user)).get_json()
    check(len(patients) == 1 and patients[0]["patient"]["id"] == patient.id, "le patient apparaît chez le médecin")

    print("3. Seul le médecin autorisé modifie")
    payload = {"value": "AS", "flag": "to_check", "interpretation": "Porteuse du trait", "exam_date": "2026-10-01"}
    check(client.put(f"{base}/exams/HB_ELECTROPHORESIS", json=payload, headers=auth(patient_user)).status_code == 403, "le patient ne modifie pas")
    check(client.put(f"{base}/exams/HB_ELECTROPHORESIS", json=payload, headers=auth(admin)).status_code == 403, "le super-administrateur ne modifie pas")
    check(client.put(f"{base}/exams/HB_ELECTROPHORESIS", json=payload, headers=auth(intruder_user)).status_code == 403, "un autre médecin ne modifie pas")
    r = client.put(f"{base}/exams/HB_ELECTROPHORESIS", json=payload, headers=auth(doc_user))
    exam = r.get_json()["exam"]
    check(r.status_code == 200 and exam["value"] == "AS" and exam["updated_by"]["id"] == doctor.id, "le médecin autorisé enregistre")
    check(exam["updated_at"] is not None, "date de dernière mise à jour fixée par le serveur")
    check(client.put(f"{base}/exams/SEMEN_VOLUME", json={"value": "2"}, headers=auth(doc_user)).status_code == 400, "examen masculin refusé pour une patiente")
    tomorrow = (services.now() + datetime.timedelta(days=1)).date().isoformat()
    check(client.put(f"{base}/exams/HIV", json={"value": "Négative", "exam_date": tomorrow}, headers=auth(doc_user)).status_code == 400, "date future refusée")
    check(client.put(f"{base}/exams/HIV", json={"flag": "bizarre"}, headers=auth(doc_user)).status_code == 400, "appréciation invalide refusée")

    print("4. Historique complet")
    client.put(f"{base}/exams/HB_ELECTROPHORESIS", json={**payload, "value": "AA", "flag": "normal"}, headers=auth(doc_user))
    client.put(f"{base}/exams/HB_ELECTROPHORESIS", json={**payload, "value": "AA", "flag": "normal"}, headers=auth(doc_user))  # identique
    client.put(f"{base}/exams/HB_ELECTROPHORESIS", json={}, headers=auth(doc_user))
    rows = client.get(f"{base}/history", headers=auth(doc_user)).get_json()
    check([row["action"] for row in rows] == ["clear", "update", "create"], "création, modification, effacement (pas d'entrée sans changement)")
    check(rows[1]["before"]["value"] == "AS" and rows[1]["after"]["value"] == "AA", "valeurs avant et après conservées")
    check(client.get("/premarital/me/history", headers=auth(patient_user)).get_json() == rows, "le patient voit le même historique")
    notes = db.session.execute(select(Notifications).filter_by(user_id=patient_user.id)).scalars().all()
    check(len(notes) == 1, "une seule notification au patient malgré plusieurs modifications")

    print("5. Retrait de l'autorisation")
    auth_id = client.get("/premarital/me", headers=auth(patient_user)).get_json()["doctors"][0]["id"]
    check(client.delete(f"/premarital/me/doctors/{auth_id}", headers=auth(other_user)).status_code == 404, "un autre patient ne retire pas l'accès")
    check(client.delete(f"/premarital/me/doctors/{auth_id}", headers=auth(patient_user)).status_code == 200, "le patient retire l'accès")
    check(client.put(f"{base}/exams/HIV", json={"value": "Négative"}, headers=auth(doc_user)).status_code == 403, "le médecin ne modifie plus")
    check(client.get("/premarital/doctor/patients", headers=auth(doc_user)).get_json() == [], "le patient disparaît de sa liste")

    print("6. Catalogue (super-administrateur)")
    new = {"code": "vitamin_d", "sex": "all", "label_fr": "Vitamine D", "reference_fr": "30 à 100 ng/mL", "unit": "ng/mL"}
    check(client.post("/premarital/catalogue", json=new, headers=auth(doc_user)).status_code == 403, "réservé au super-administrateur")
    r = client.post("/premarital/catalogue", json=new, headers=auth(admin))
    created = r.get_json()["exam"]
    check(r.status_code == 201 and created["code"] == "VITAMIN_D", "examen ajouté (code normalisé)")
    check(client.post("/premarital/catalogue", json=new, headers=auth(admin)).status_code == 409, "code en double refusé")
    check(any(e["code"] == "VITAMIN_D" for e in client.get("/premarital/me", headers=auth(patient_user)).get_json()["exams"]), "visible chez le patient")
    hb_id = next(e["id"] for e in client.get("/premarital/catalogue", headers=auth(admin)).get_json() if e["code"] == "HB_ELECTROPHORESIS")
    client.put(f"/premarital/catalogue/{hb_id}", json={"is_active": False}, headers=auth(admin))
    exams = client.get("/premarital/me", headers=auth(patient_user)).get_json()["exams"]
    hb = next((e for e in exams if e["code"] == "HB_ELECTROPHORESIS"), None)
    check(hb is not None and hb["is_active"] is False, "examen retiré mais déjà renseigné : toujours visible chez le patient")
    client.post("/premarital/me/doctors", json={"matricule": "MED-000"}, headers=auth(patient_user))
    check(client.put(f"{base}/exams/HB_ELECTROPHORESIS", json={"value": "AA"}, headers=auth(doc_user)).status_code == 400, "examen retiré : plus modifiable")

    print("7. Données personnelles")
    export = privacy.export_data(patient_user)
    check(len(export["premarital_checkup"]["history"]) == 3, "le bilan figure dans l'export du compte")
    privacy.anonymize_account(patient_user, admin.id)
    db.session.commit()
    check(db.session.execute(select(PremaritalExam).filter_by(patient_id=patient.id)).first() is None, "bilan effacé avec le compte")
    check(db.session.execute(select(PremaritalExamHistory).filter_by(patient_id=patient.id)).first() is None, "historique effacé avec le compte")

print(f"\n{ok_count} contrôles réussis.")
