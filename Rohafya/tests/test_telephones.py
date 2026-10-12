"""Numéros de téléphone des comptes patient et médecin (SQLite en mémoire).

Un numéro n'appartient qu'à un seul compte ; le téléphone secondaire est facultatif.

Lancement, depuis la racine du dépôt :
    Rohafya/envDoc/bin/python Rohafya/tests/test_telephones.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flask import Flask
from flask_jwt_extended import create_access_token

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
)
db.init_app(app)
jwt.init_app(app)
login_manager.init_app(app)

from sqlalchemy import JSON, null

from Rohafya.accounts.models import Doctors, Patients, User, UserActivity  # noqa: E402
from Rohafya.accounts.phones import PHONES_IDENTICAL, clean_phone, phone_error, phone_key  # noqa: E402
from Rohafya.custom.customization import CustomUserManager  # noqa: E402

CustomUserManager(app, db, User)


@jwt.user_identity_loader
def _identity(user):
    return user


@jwt.user_lookup_loader
def _lookup(_header, data):
    return db.session.get(User, int(data["sub"]))


from Rohafya.doctors.charge_doctors import gnu_doctors  # noqa: E402
from Rohafya.patients.charge_patients import charges_patients  # noqa: E402

for bp in (charges_patients, gnu_doctors):
    app.register_blueprint(bp)

# Les permissions relèvent d'un autre test : ici, tout utilisateur connecté est autorisé.
User.has_permission = lambda self, code: True
UserActivity.__table__.c.route.type = JSON()

ok_count = 0


def check(condition, label):
    global ok_count
    if not condition:
        raise AssertionError(label)
    ok_count += 1
    print("  ✔", label)


with app.app_context():
    db.create_all()
    client = app.test_client()

    def account(username, **fields):
        user = User(username=username, email=f"{username}@test", password="x")
        db.session.add(user)
        db.session.flush()
        model = Doctors if "DoctorPhone" in fields else Patients
        email = "DoctorEmail" if model is Doctors else "PatientEmail"
        # NULL explicite : omis, un numéro vaudrait la valeur par défaut '' de la colonne.
        fields = {k: null() if v is None else v for k, v in fields.items()}
        row = model(user_id=user.id, **{email: f"{username}@test"}, **fields)
        db.session.add(row)
        db.session.commit()
        return user, row

    def auth(user):
        return {"Authorization": "Bearer " + create_access_token(identity=str(user.id))}

    print("1. Normalisation des numéros")
    check(clean_phone("  ") is None and clean_phone(None) is None, "un numéro vide vaut NULL")
    check(clean_phone(" 695 99 58 42 ") == "695 99 58 42", "les espaces superflus sont retirés")
    check(phone_key("+237 695 99 58 42") == phone_key("00237695995842") == phone_key("695995842"), "l'indicatif 237 est ignoré")

    ua, alice = account("alice", PatientPhone="695995842", PatientPhone2=None)
    ub, bob = account("bob", PatientPhone="677000001", PatientPhone2="677000002")

    print("2. Contrôle avant écriture")
    check(phone_error(Patients, ("PatientPhone", "PatientPhone2"), ("+237 695 99 58 42", None), bob.id) is not None,
          "numéro principal d'un autre compte refusé, quel que soit son format")
    check(phone_error(Patients, ("PatientPhone", "PatientPhone2"), ("677000002", None), alice.id) is not None,
          "numéro secondaire d'un autre compte refusé")
    check(phone_error(Patients, ("PatientPhone", "PatientPhone2"), ("695995842", "695 995 842"), alice.id) == PHONES_IDENTICAL,
          "deux numéros identiques sur un même compte refusés")
    check(phone_error(Patients, ("PatientPhone", "PatientPhone2"), ("695995842", None), alice.id) is None,
          "le compte garde son propre numéro")

    print("3. Paramètres du patient (PUT /patient/update/<id>)")
    url = f"/patient/update/{alice.id}"
    r = client.put(url, json={"PatientName": "Alice", "PatientPhone": "695995842", "PatientPhone2": ""}, headers=auth(ua))
    check(r.status_code == 200, "téléphone 2 vide accepté")
    check(db.session.get(Patients, alice.id).PatientPhone2 is None, "téléphone 2 vide enregistré à NULL")
    r = client.put(f"/patient/update/{bob.id}", json={"PatientPhone": "677000001", "PatientPhone2": ""}, headers=auth(ub))
    check(r.status_code == 200, "deux comptes sans téléphone 2 coexistent")
    r = client.put(url, json={"PatientPhone": "677 00 00 01"}, headers=auth(ua))
    check(r.status_code == 409 and "677 00 00 01" in r.get_json()["message"], "numéro d'un autre compte : 409 avec message explicite")
    check(db.session.get(Patients, alice.id).PatientPhone == "695995842", "le numéro refusé n'est pas enregistré")
    r = client.put(url, json={"PatientPhone": ""}, headers=auth(ua))
    check(r.status_code == 400 and "obligatoire" in r.get_json()["message"], "téléphone principal obligatoire")
    r = client.put(url, json={"PatientPhone": "695995842", "PatientPhone2": "695995842"}, headers=auth(ua))
    check(r.status_code == 400 and r.get_json()["message"] == PHONES_IDENTICAL, "numéros identiques : 400")
    r = client.put("/patient/update/9999", json={}, headers=auth(ua))
    check(r.status_code == 404 and r.get_json()["message"], "patient inconnu : 404 avec message")

    print("4. Paramètres du médecin (PUT /doctors/update/<id>)")
    uc, carl = account("carl", DoctorPhone="690000001", DoctorPhone2=None, DoctorName="Carl")
    ud, dina = account("dina", DoctorPhone="690000002", DoctorPhone2=None, DoctorName="Dina")
    r = client.put(f"/doctors/update/{carl.id}", json={"DoctorPhone": "690000001", "DoctorPhone2": "", "DoctorNat": "Camerounaise"}, headers=auth(uc))
    check(r.status_code == 200 and db.session.get(Doctors, carl.id).DoctorPhone2 is None, "téléphone 2 du médecin facultatif (NULL)")
    check(db.session.get(Doctors, carl.id).DoctorNat == "Camerounaise", "la nationalité est bien enregistrée")
    r = client.put(f"/doctors/update/{dina.id}", json={"DoctorPhone2": "+237 690 00 00 01"}, headers=auth(ud))
    check(r.status_code == 409 and "déjà utilisé" in r.get_json()["message"], "numéro d'un autre médecin : 409 avec message explicite")

print(f"\n{ok_count} contrôles réussis.")
