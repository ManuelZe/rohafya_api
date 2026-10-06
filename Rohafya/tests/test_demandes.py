"""Prescriptions, pré-enregistrements et requêtes adressés aux établissements (SQLite en mémoire).

Lancement, depuis la racine du dépôt :
    Rohafya/envDoc/bin/python Rohafya/tests/test_demandes.py
"""
import io
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flask import Flask
from flask_jwt_extended import create_access_token
from sqlalchemy import JSON, select

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

from Rohafya.accounts.models import Doctors, Notifications, Prescriptions, Requests, SavePatients, User, UserActivity  # noqa: E402
from Rohafya.custom.customization import CustomUserManager  # noqa: E402

CustomUserManager(app, db, User)


@jwt.user_identity_loader
def _identity(user):
    return user


@jwt.user_lookup_loader
def _lookup(_header, data):
    return db.session.get(User, int(data["sub"]))


from Rohafya.accounts.requests2 import requete  # noqa: E402
from Rohafya.patients.prescriptions import prescriptions  # noqa: E402
from Rohafya.patients.save_patients import save_patients  # noqa: E402
from Rohafya.saas import services, submissions  # noqa: E402
from Rohafya.saas.cli import saas_cli  # noqa: E402
from Rohafya.saas.models import Submission, Tenant  # noqa: E402
from Rohafya.saas.submission_routes import submissions_admin  # noqa: E402
from Rohafya.saas.super_admin_routes import super_admin  # noqa: E402
import Rohafya.saas.super_admin_routes as sar  # noqa: E402

for bp in (prescriptions, save_patients, requete, submissions_admin, super_admin):
    app.register_blueprint(bp)
app.cli.add_command(saas_cli)

SENT = []


def fake_send(to, subject, template, **context):
    SENT.append({"to": to, "subject": subject, "template": template, **context})
    return True


submissions.send_mail = fake_send
sar.send_mail = fake_send
UserActivity.__table__.c.route.type = JSON()

ok_count = 0


def check(condition, label):
    global ok_count
    if not condition:
        raise AssertionError(label)
    ok_count += 1
    print("  ✔", label)


def image(name="ordonnance.png"):
    return (io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 64), name)


with app.app_context():
    db.create_all()
    client = app.test_client()

    def auth(user):
        return {"Authorization": "Bearer " + create_access_token(identity=str(user.id))}

    print("0. Préparation : établissements, rôles et comptes")
    runner = app.test_cli_runner()
    runner.invoke(args=["saas", "init"])
    pdmd = services.default_gnuhealth_tenant()
    clinique = Tenant(slug="clinique-a", name="Clinique A", source_type="api")
    labo = Tenant(slug="labo-b", name="Labo B", source_type="api")
    fermee = Tenant(slug="fermee", name="Fermée", source_type="api", is_active=False)
    db.session.add_all([clinique, labo, fermee])
    db.session.commit()

    patient_user = services.create_user("aicha@example.com", "Aïcha", "Ngono", "Patient")
    patient = services.ensure_patient_profile(patient_user)
    other_user = services.create_user("autre@example.com", "Autre", "Patient", "Patient")
    services.ensure_patient_profile(other_user)
    doctor_user = services.create_user("dr@example.com", "Paul", "Mbarga", "Doctor")
    db.session.add(Doctors(user_id=doctor_user.id, DoctorName="Paul", DoctorLastname="Mbarga", DoctorNO="ONMC-1234",
                           DoctorEmail="dr@example.com", DoctorPhone="690000001", DoctorPhone2="690000002"))
    admin_a = services.create_user("admin.a@example.com", "Admin", "A", "EstablishmentAdmin")
    admin_b = services.create_user("admin.b@example.com", "Admin", "B", "EstablishmentAdmin")
    services.ensure_tenant_admin(admin_a, clinique)
    services.ensure_tenant_admin(admin_b, labo)
    db.session.commit()
    check(patient_user.has_permission("patients.patients_prescriptions.add_prescriptions")
          and doctor_user.has_permission("doctors.prescriptions.add_prescriptions")
          and doctor_user.has_permission("doctors.saved_patients.add_save_patients"), "permissions Patient / Doctor attribuées par init")

    print("1. Établissements proposés")
    r = client.get("/saas/public/establishments")
    noms = [e["name"] for e in r.get_json()]
    check(r.status_code == 200 and "Clinique A" in noms and "PDMD Santé" in noms and "Fermée" not in noms,
          "liste publique des établissements actifs (sans connexion)")

    print("2. Patient : envoi à un établissement")
    form = {"NameDoctor": "Dr Kamga", "OrdreDoctor": "ONMC-9", "Description": "NFS", "demande_devis": "true", "audience": "patient"}
    r = client.post("/prescription/add/", data={**form, "file": image()}, headers=auth(patient_user), content_type="multipart/form-data")
    check(r.status_code == 400 and "établissement" in r.get_json()["message"], "établissement obligatoire")
    r = client.post("/prescription/add/", data={**form, "tenant_id": str(fermee.id)}, headers=auth(patient_user))
    check(r.status_code == 400, "établissement inactif refusé")
    SENT.clear()
    r = client.post("/prescription/add/", data={**form, "tenant_id": str(clinique.id), "file": image()},
                    headers=auth(patient_user), content_type="multipart/form-data")
    presc = r.get_json()["Prescription"]
    check(r.status_code == 201 and presc["submission"]["establishment"] == "Clinique A" and presc["submission"]["status"] == "recue",
          "prescription envoyée à la Clinique A (statut « reçue »)")
    check([m["to"] for m in SENT] == ["admin.a@example.com"] and SENT[0]["template"] == "emails/nouvelle_demande.html",
          "seuls les administrateurs de la Clinique A sont prévenus par e-mail")
    check(db.session.execute(select(Notifications).filter_by(user_id=admin_a.id)).first() is not None, "et par notification")
    r = client.post("/save_patient/add/", data={"nom": "Ngono", "prenom": "Fils", "description": "Enfant", "tenant_id": str(clinique.id),
                                                "file": image("cni.jpg")}, headers=auth(patient_user), content_type="multipart/form-data")
    check(r.status_code == 201 and r.get_json()["submission"]["kind"] == "pre_enregistrement", "pré-enregistrement envoyé")
    pre_id = r.get_json()["id"]
    r = client.post("/requete/add", json={"first_name": "Aïcha", "last_name": "Ngono", "message": "Prix d'une NFS ?",
                                          "patient_request_prix_examen": True, "tenant_id": labo.id, "audience": "patient"},
                    headers=auth(patient_user))
    check(r.status_code == 201 and r.get_json()["submission"]["establishment"] == "Labo B" and r.get_json()["email"] == "aicha@example.com",
          "requête envoyée au Labo B (e-mail du compte repris)")
    req_id = r.get_json()["id"]

    print("3. Médecin : mêmes envois, au nom de ses patients")
    r = client.post("/prescription/add/", data={"Description": "Bilan", "tenant_id": str(clinique.id), "audience": "doctor"},
                    headers=auth(doctor_user))
    check(r.status_code == 400 and "patient concerné" in r.get_json()["message"], "patient concerné obligatoire")
    r = client.post("/prescription/add/", data={"Description": "Bilan lipidique", "patient_name": "Jean Essomba",
                                                "tenant_id": str(clinique.id), "audience": "doctor", "file": image()},
                    headers=auth(doctor_user), content_type="multipart/form-data")
    dpresc = r.get_json()["Prescription"]
    check(r.status_code == 201 and dpresc["NameDoctor"] == "Dr Paul Mbarga" and dpresc["OrdreDoctor"] == "ONMC-1234"
          and dpresc["patient_id"] is None and dpresc["submission"]["patient_name"] == "Jean Essomba",
          "prescription du médecin : nom et ordre repris du profil, patient concerné")
    r = client.post("/save_patient/add/", data={"nom": "Essomba", "prenom": "Jean", "tenant_id": str(clinique.id), "audience": "doctor",
                                                "file": image("cni.png")}, headers=auth(doctor_user), content_type="multipart/form-data")
    check(r.status_code == 201 and r.get_json()["patient_id"] is None and r.get_json()["submission"]["author_role"] == "doctor",
          "pré-enregistrement d'un patient par le médecin")
    r = client.post("/requete/add", json={"first_name": "Paul", "last_name": "Mbarga", "message": "Commission de septembre",
                                          "commission": True, "tenant_id": clinique.id, "audience": "doctor"}, headers=auth(doctor_user))
    check(r.status_code == 201 and r.get_json()["submission"]["author_role"] == "doctor", "requête du médecin")
    r = client.post("/requete/add", json={"first_name": "A", "last_name": "N", "message": "x", "tenant_id": clinique.id,
                                          "audience": "doctor"}, headers=auth(patient_user))
    check(r.status_code == 403, "un patient ne peut pas écrire comme médecin")
    r = client.get("/prescription/all_prescriptions/?audience=doctor", headers=auth(doctor_user))
    check([p["id"] for p in r.get_json()] == [dpresc["id"]], "espace médecin : ses seules prescriptions")

    print("4. Visiteur sans compte")
    r = client.post("/requete/anonym/add", json={"first_name": "Marie", "last_name": "Tchoua", "message": "Horaires ?", "tenant_id": labo.id})
    check(r.status_code == 400 and "e-mail" in r.get_json()["message"], "e-mail obligatoire pour recevoir la réponse")
    r = client.post("/requete/add", json={"first_name": "Marie", "last_name": "Tchoua", "email": "marie@example.com",
                                          "message": "Horaires ?", "tenant_id": labo.id})
    check(r.status_code == 201 and r.get_json()["submission"]["author_role"] == "anonyme", "requête anonyme au Labo B")
    anon_req = r.get_json()["id"]

    print("5. Cloisonnement")
    r = client.get("/prescription/all_prescriptions/?audience=patient", headers=auth(other_user))
    check(r.get_json() == [], "un autre patient ne voit pas ces prescriptions")
    check(client.get(f"/prescription/image/{presc['id']}", headers=auth(other_user)).status_code == 403, "ni leur image")
    check(client.delete(f"/prescription/del/{presc['id']}", headers=auth(other_user)).status_code == 403, "ni ne peut les supprimer")
    check(client.get(f"/requete/get_requests/{patient_user.id}", headers=auth(other_user)).status_code == 403,
          "ni lire les requêtes d'un autre")
    r = client.get(f"/saas/admin/tenants/{clinique.id}/submissions", headers=auth(admin_b))
    check(r.status_code == 403, "l'administrateur du Labo B n'accède pas à la Clinique A")
    r = client.get(f"/saas/admin/tenants/{labo.id}/submissions", headers=auth(admin_b))
    check(r.get_json()["total"] == 2 and {i["kind"] for i in r.get_json()["items"]} == {"requete"}, "le Labo B ne voit que ses 2 requêtes")

    print("6. Console de la Clinique A")
    r = client.get(f"/saas/admin/tenants/{clinique.id}/submissions", headers=auth(admin_a))
    body = r.get_json()
    check(r.status_code == 200 and body["total"] == 5, "5 demandes reçues (2 prescriptions, 2 pré-enregistrements, 1 requête)")
    check(body["counts"]["prescription"]["recue"] == 2 and body["counts"]["pre_enregistrement"]["recue"] == 2, "compteurs par type et statut")
    r = client.get(f"/saas/admin/tenants/{clinique.id}/submissions?kind=prescription&q=essomba", headers=auth(admin_a))
    check(r.get_json()["total"] == 1 and r.get_json()["items"][0]["author"]["name"] == "Dr Paul Mbarga", "recherche par patient concerné")
    item = next(i for i in body["items"] if i["kind"] == "prescription" and i["author"]["role"] == "patient")
    check(item["author"]["email"] == "aicha@example.com" and item["has_image"] and item["item"]["demande_devis"], "détail : auteur, image, devis")
    r = client.get(f"/saas/admin/tenants/{clinique.id}/submissions/{item['id']}/image", headers=auth(admin_a))
    check(r.status_code == 200 and r.data.startswith(b"\x89PNG"), "image jointe consultable par l'établissement")
    check(client.get(f"/prescription/image/{presc['id']}", headers=auth(patient_user)).status_code == 200, "et par son auteur (route d'origine)")

    print("7. Réponse de l'établissement")
    SENT.clear()
    r = client.put(f"/saas/admin/tenants/{clinique.id}/submissions/{item['id']}",
                   json={"status": "traitee", "response": "Devis prêt.\nPassez à l'accueil.", "quote_amount": "15000"}, headers=auth(admin_a))
    check(r.status_code == 200 and r.get_json()["status"] == "traitee" and r.get_json()["quote_amount"] == 15000.0, "statut, réponse et devis")
    check(SENT and SENT[-1]["to"] == "aicha@example.com" and SENT[-1]["template"] == "emails/reponse_demande.html"
          and SENT[-1]["quote_amount"] == 15000.0, "l'auteur reçoit la réponse par e-mail")
    check(db.session.execute(select(Notifications).filter_by(user_id=patient_user.id)).first() is not None, "et une notification")
    r = client.get("/prescription/all_prescriptions/?audience=patient", headers=auth(patient_user))
    mine = r.get_json()[0]["submission"]
    check(mine["status_label"] == "Traitée" and mine["response"].startswith("Devis prêt") and mine["quote_amount"] == 15000.0,
          "le patient voit la réponse dans son espace")
    pre_sub = next(i for i in body["items"] if i["kind"] == "pre_enregistrement" and i["item"]["id"] == pre_id)
    client.put(f"/saas/admin/tenants/{clinique.id}/submissions/{pre_sub['id']}", json={"status": "traitee"}, headers=auth(admin_a))
    db.session.expire_all()
    check(db.session.get(SavePatients, pre_id).validated is True, "pré-enregistrement traité = validé (ancien champ)")
    r = client.put(f"/saas/admin/tenants/{clinique.id}/submissions/{item['id']}", json={"status": "inconnu"}, headers=auth(admin_a))
    check(r.status_code == 400, "statut inconnu refusé")
    lab_req = client.get(f"/saas/admin/tenants/{labo.id}/submissions?kind=requete", headers=auth(admin_b)).get_json()["items"]
    anon_sub = next(i for i in lab_req if i["item"]["id"] == anon_req)
    SENT.clear()
    client.put(f"/saas/admin/tenants/{labo.id}/submissions/{anon_sub['id']}", json={"status": "refusee", "response": "Hors délai."},
               headers=auth(admin_b))
    db.session.expire_all()
    check(SENT[-1]["to"] == "marie@example.com" and not SENT[-1]["has_account"], "le visiteur anonyme reçoit la réponse par e-mail")
    check(db.session.get(Requests, anon_req).rejected is True, "requête refusée = rejected (ancien champ)")
    r = client.put(f"/saas/admin/tenants/{clinique.id}/submissions/{anon_sub['id']}", json={"status": "traitee"}, headers=auth(admin_a))
    check(r.status_code == 404, "une demande d'un autre établissement est introuvable")

    print("8. Suppression par l'auteur")
    r = client.delete(f"/requete/del/{req_id}", headers=auth(patient_user))
    check(r.status_code == 200 and db.session.execute(select(Submission).filter_by(kind="requete", item_id=req_id)).first() is None,
          "requête et adressage supprimés")

    print("9. Demandes antérieures → PDMD Santé (flask saas init)")
    old_p = Prescriptions(NameDoctor="Dr Ancien", patient_id=patient.id)
    old_s = SavePatients(nom="Ancien", prenom="Pré", patient_id=patient.id, validated=True)
    old_r = Requests(first_name="Ancien", last_name="Visiteur", email="v@example.com", message="?", valide=True)
    db.session.add_all([old_p, old_s, old_r])
    db.session.commit()
    r = client.get("/prescription/all_prescriptions/?audience=patient", headers=auth(patient_user))
    check(any(p["id"] == old_p.id and p["submission"] is None for p in r.get_json()), "avant init : visibles par leur auteur, sans établissement")
    sortie = runner.invoke(args=["saas", "init"]).output
    check("1 prescription(s), 1 pré-enregistrement(s), 1 requête(s)" in sortie, "init les rattache")
    subs = {s.kind: s for s in db.session.execute(select(Submission).filter_by(tenant_id=pdmd.id)).scalars()}
    check(set(subs) == {"prescription", "pre_enregistrement", "requete"} and subs["prescription"].author_id == patient_user.id,
          "à PDMD Santé, avec leur auteur")
    check(subs["pre_enregistrement"].status == "traitee" and subs["requete"].status == "traitee", "statut repris des anciens champs")
    check("0 prescription(s), 0 pré-enregistrement(s), 0 requête(s)" in runner.invoke(args=["saas", "init"]).output, "init relancé : rien de plus")

    print("10. Suppression d'un établissement : ses demandes partent avec lui")
    root = services.create_user("root@example.com", "Root", "Admin", "SuperAdmin")
    db.session.commit()
    labo_items = db.session.execute(select(Submission.item_id).filter_by(tenant_id=labo.id)).scalars().all()
    r = client.delete(f"/saas/super/tenants/{labo.id}", json={"confirm_slug": "labo-b"}, headers=auth(root))
    check(r.status_code == 200 and r.get_json()["deleted"]["demandes"] == len(labo_items) == 1, "demande du Labo B comptée dans la suppression")
    db.session.expire_all()
    check(all(db.session.get(Requests, item_id) is None for item_id in labo_items), "son contenu (requête) est effacé")
    sortie = runner.invoke(args=["saas", "init"]).output
    check("0 prescription(s), 0 pré-enregistrement(s), 0 requête(s)" in sortie, "rien n'est réattribué à PDMD Santé")

print(f"\n{ok_count} vérifications réussies.")
