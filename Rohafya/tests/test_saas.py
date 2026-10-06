"""Test de bout en bout du module SaaS, sur SQLite en mémoire (sans PostgreSQL ni GNU Health).

Lancement, depuis la racine du dépôt :
    Rohafya/envDoc/bin/python Rohafya/tests/test_saas.py
"""
import datetime
import io
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
    SECRET_KEY="test-secret",
    JWT_SECRET_KEY="test-jwt",
    JWT_TOKEN_LOCATION=["headers"],
    USER_APP_NAME="ROHAFYA",
    USER_ENABLE_EMAIL=True,
    USER_ENABLE_USERNAME=True,
    USER_EMAIL_SENDER_EMAIL="noreply@test",
    MAIL_DEFAULT_SENDER="noreply@test",
    MAIL_SUPPRESS_SEND=True,
    TESTING=True,
    ROHAFYA_FRONT_URL="https://rohafya.test",
)
db.init_app(app)
jwt.init_app(app)
login_manager.init_app(app)

from Rohafya.accounts.models import Patients, User, UserActivity  # noqa: E402
from Rohafya.custom.customization import CustomUserManager  # noqa: E402

CustomUserManager(app, db, User)


@jwt.user_identity_loader
def _identity(user):
    return user


@jwt.user_lookup_loader
def _lookup(_header, data):
    return db.session.get(User, int(data["sub"]))


from Rohafya.saas import services  # noqa: E402
from Rohafya.saas.account_routes import saas_account  # noqa: E402
from Rohafya.saas.ingest_routes import fhir, ingest  # noqa: E402
from Rohafya.saas.super_admin_routes import super_admin  # noqa: E402
from Rohafya.saas.tenant_admin_routes import tenant_admin  # noqa: E402
from Rohafya.saas.pdf_routes import pdf_imports  # noqa: E402

for bp in (saas_account, tenant_admin, super_admin, ingest, fhir, pdf_imports):
    app.register_blueprint(bp)

SENT = []


def fake_send(to, subject, template, **context):
    SENT.append({"to": to, "subject": subject, "template": template, **context})
    return True


services.send_mail = fake_send
import Rohafya.saas.super_admin_routes as sar  # noqa: E402

sar.send_mail = fake_send

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

    def auth(user):
        return {"Authorization": "Bearer " + create_access_token(identity=str(user.id))}

    print("1. Connexion par code e-mail et création de compte patient")
    r = client.post("/saas/auth/otp/request", json={"email": "Aicha@Example.com"})
    check(r.status_code == 200, "code demandé")
    code = SENT[-1]["code"]
    check(SENT[-1]["to"] == "aicha@example.com" and len(code) == 6, "e-mail envoyé (adresse normalisée)")
    r = client.post("/saas/auth/otp/verify", json={"email": "aicha@example.com", "code": "000000" if code != "000000" else "111111"})
    check(r.status_code == 400, "mauvais code refusé")
    r = client.post("/saas/auth/otp/verify", json={"email": "aicha@example.com", "code": code})
    check(r.get_json() == {"needs_registration": True}, "compte inexistant → demande nom/prénom")
    r = client.post("/saas/auth/otp/verify", json={"email": "aicha@example.com", "code": code, "first_name": "Aïcha", "last_name": "Ngono"})
    body = r.get_json()
    check(r.status_code == 200 and body.get("access_token"), "compte créé et connecté")
    check([role["name"] for role in body["data"]["roles"]] == ["Patient"] and body["data"]["patient_id"], "rôle Patient + profil patient")
    patient_user = db.session.get(User, body["data"]["id"])
    patient = patient_user.patients
    check(patient.PatientFederationID.startswith("ROHAFYA-"), "identifiant ROHAFYA attribué")
    r = client.post("/saas/auth/otp/verify", json={"email": "aicha@example.com", "code": code})
    check(r.status_code == 400, "code déjà utilisé refusé")

    print("2. Super-administrateur : création d'un établissement")
    admin = services.create_user("root@rohafya.test", "Root", "Admin", "SuperAdmin")
    db.session.commit()
    r = client.post("/saas/super/tenants", json={"name": "Clinique Béthanie", "source_type": "api"}, headers=auth(admin))
    tenant = r.get_json()
    check(r.status_code == 201 and tenant["slug"] == "clinique-bethanie" and tenant["api_key"].startswith("rohafya_"), "établissement créé avec clé d'API")
    api = {"X-ROHAFYA-API-Key": tenant["api_key"]}
    r = client.post("/saas/super/tenants", json={"name": "Autre"}, headers=auth(patient_user))
    check(r.status_code == 403, "un patient ne peut pas créer d'établissement")

    print("3. Envoi de données par l'API ROHAFYA (format de la démo)")
    r = client.get("/ingest/v1/ping", headers=api)
    check(r.status_code == 200 and r.get_json()["slug"] == "clinique-bethanie", "clé d'API reconnue")
    check(client.get("/ingest/v1/ping", headers={"X-ROHAFYA-API-Key": "faux"}).status_code == 401, "mauvaise clé refusée")
    check(client.get("/ingest/v1/ping", headers={"X-EDEN-API-Key": tenant["api_key"]}).status_code == 200, "ancien en-tête X-EDEN-API-Key accepté")
    r = client.post("/ingest/v1/patients", json=[{"local_ref": "P-88-17", "first_name": "Aïcha", "last_name": "Ngono", "email": "aicha@example.com"}], headers=api)
    check(r.get_json() == {"created": 1, "updated": 0, "total": 1}, "patient reçu")
    lab = {
        "local_ref": "P-88-17", "name": "LAB-0412", "test": "NFS", "validation_date": "2026-09-27T09:00:00",
        "details": [{"name": "Hémoglobine", "result": 11.4, "units": "g/dl", "warning": True}],
    }
    r = client.post("/ingest/v1/laboratoire", json={"records": [lab]}, headers=api)
    check(r.get_json()["created"] == 1, "résultat de laboratoire reçu")
    r = client.post("/ingest/v1/laboratoire", json=[{"local_ref": "P-88-17"}], headers=api)
    check(r.status_code == 400 and "name" in r.get_json()["message"], "champ obligatoire manquant refusé")
    invoice = {"local_ref": "P-88-17", "reference": "FAC-001", "date": "2026-09-27", "state": "posted", "untaxed_amount": 10000,
               "amount_to_pay_today": 5000, "total_amount2": 10000, "products": [{"product_name": "NFS", "quantity": "1"}]}
    r = client.post("/ingest/v1/factures", json=[invoice], headers=api)
    check(r.get_json()["created"] == 1, "facture reçue")

    print("4. Administrateur d'établissement")
    r = client.post(f"/saas/super/tenants/{tenant['id']}/admins", json={"email": "admin@bethanie.test", "first_name": "Marc", "last_name": "Owona"}, headers=auth(admin))
    check(r.status_code == 201 and r.get_json()["email_sent"], "administrateur invité par e-mail")
    tenant_admin_user = services.active_users_by_email("admin@bethanie.test")[0]
    r = client.get(f"/saas/admin/tenants/{tenant['id']}/dashboard", headers=auth(tenant_admin_user))
    stats = r.get_json()
    check(r.status_code == 200 and stats["patients_known"] == 1 and stats["records"]["laboratoire"] == 1, "tableau de bord de l'établissement")
    r = client.get(f"/saas/admin/tenants/{tenant['id']}/patients?q=ngono", headers=auth(tenant_admin_user))
    check(r.get_json()["items"][0]["link_status"] == "none", "patient listé, pas encore rattaché")
    r = client.get(f"/saas/admin/tenants/{tenant['id']}/dashboard", headers=auth(patient_user))
    check(r.status_code == 403, "un patient n'accède pas au back-office")
    r = client.get("/saas/me", headers=auth(tenant_admin_user))
    check([t["id"] for t in r.get_json()["admin_tenants"]] == [tenant["id"]], "/saas/me liste l'établissement administré")

    print("5. QR code et rattachement")
    r = client.post(f"/saas/admin/tenants/{tenant['id']}/link-tokens", json={"local_ref": "P-88-17"}, headers=auth(tenant_admin_user))
    token = r.get_json()
    check(token["url"].startswith("https://rohafya.test/l/") and token["qr_png"] and len(token["short_code"]) == 8, "QR code généré (URL, PNG, code court)")
    raw = token["url"].rsplit("/", 1)[1]
    r = client.get(f"/saas/public/link-tokens/{raw}")
    check(r.get_json()["establishment"] == "Clinique Béthanie", "infos publiques du QR")
    r = client.post("/saas/me/links/redeem", json={"code": raw}, headers=auth(patient_user))
    check(r.status_code == 200 and r.get_json()["link"]["status"] == "active", "rattaché immédiatement (e-mail identique)")
    r = client.post("/saas/me/links/redeem", json={"code": raw}, headers=auth(patient_user))
    check(r.status_code == 410, "QR à usage unique")
    r = client.post(f"/saas/admin/tenants/{tenant['id']}/link-tokens", json={"local_ref": "P-88-17"}, headers=auth(tenant_admin_user))
    check(r.get_json() == {"already_linked": True, "local_ref": "P-88-17"}, "dossier déjà rattaché : pas de nouveau QR")

    print("6. Données visibles par le patient, règles de l'établissement")
    records = services.patient_records(patient, "laboratoire")
    check(len(records) == 1 and records[0]["establishment"] == "Clinique Béthanie" and records[0]["statut_expiration"] is False, "résultat agrégé avec son établissement")
    record, rtenant = services.find_patient_record(patient, "laboratoire", "LAB-0412")
    body, status = services.record_details_response(record, rtenant)
    check(status == 403, "détails bloqués : facture impayée")
    invoice["state"] = "paid"
    client.post("/ingest/v1/factures", json=[invoice], headers=api)
    body, status = services.record_details_response(record, rtenant)
    check(status == 200 and body[0]["name"] == "Hémoglobine", "détails accessibles une fois la facture payée")

    print("7. Rattachement en attente (e-mail différent) puis validation")
    client.post("/ingest/v1/patients", json=[{"local_ref": "P-99-01", "email": "autre@example.com", "last_name": "Kamga"}], headers=api)
    r = client.post("/ingest/v1/link-tokens?qr=1", json={"local_ref": "P-99-01"}, headers=api)
    short = r.get_json()["short_code"]
    check(r.get_json()["qr_png"], "QR généré par l'API de l'établissement")
    r = client.post("/saas/me/links/redeem", json={"code": short.lower().replace("-", " ")}, headers=auth(patient_user))
    link = r.get_json()["link"]
    check(link["status"] == "pending", "code court accepté, lien en attente (e-mail différent)")
    r = client.post(f"/saas/admin/tenants/{tenant['id']}/links/{link['id']}/approve", headers=auth(tenant_admin_user))
    check(r.get_json()["status"] == "active", "l'établissement valide le lien")

    print("8. FHIR R4")
    bundle = {
        "resourceType": "Bundle", "type": "transaction",
        "entry": [
            {"fullUrl": "urn:uuid:p1", "resource": {"resourceType": "Patient", "id": "p1", "identifier": [{"value": "P-88-17"}], "name": [{"family": "Ngono", "given": ["Aïcha"]}]}},
            {"fullUrl": "urn:uuid:o1", "resource": {"resourceType": "Observation", "id": "o1", "code": {"text": "Glycémie"}, "valueQuantity": {"value": 1.4, "unit": "g/l"}, "interpretation": [{"coding": [{"code": "H"}]}], "referenceRange": [{"low": {"value": 0.7}, "high": {"value": 1.1}}]}},
            {"resource": {"resourceType": "DiagnosticReport", "id": "dr1", "identifier": [{"value": "LAB-0500"}], "category": [{"coding": [{"code": "LAB"}]}], "code": {"text": "Glycémie à jeun"}, "subject": {"reference": "urn:uuid:p1"}, "issued": "2026-09-28T10:00:00Z", "result": [{"reference": "urn:uuid:o1"}], "conclusion": "Hyperglycémie"}},
            {"resource": {"resourceType": "Invoice", "id": "inv1", "identifier": [{"value": "FAC-002"}], "status": "balanced", "subject": {"reference": "urn:uuid:p1"}, "totalGross": {"value": 3000}, "lineItem": [{"chargeItemCodeableConcept": {"text": "Glycémie"}}]}},
        ],
    }
    r = client.post("/fhir/r4", json=bundle, headers=api)
    summary = r.get_json()["summary"]
    check(summary["patients"] == 1 and summary["laboratoire"] == 1 and summary["facture"] == 1, "Bundle FHIR converti")
    record, rtenant = services.find_patient_record(patient, "laboratoire", "LAB-0500")
    body, status = services.record_details_response(record, rtenant)
    check(record.payload["test"] == "Glycémie à jeun" and body[0]["warning"] is True and body[0]["units"] == "g/l", "Observation → valeur hors norme")

    print("9. Cloisonnement entre établissements")
    r = client.post("/saas/super/tenants", json={"name": "Labo Horizon"}, headers=auth(admin))
    other = r.get_json()
    r = client.get(f"/saas/admin/tenants/{other['id']}/patients", headers=auth(tenant_admin_user))
    check(r.status_code == 403, "un admin ne voit pas un autre établissement")
    r = client.get(f"/saas/admin/tenants/{other['id']}/patients", headers=auth(admin))
    check(r.status_code == 200 and r.get_json()["total"] == 0, "l'autre établissement n'a aucun patient")
    r = client.post("/ingest/v1/laboratoire", json=[{**lab, "name": "LAB-X"}], headers={"X-ROHAFYA-API-Key": other["api_key"]})
    check(r.status_code == 200 and services.find_patient_record(patient, "laboratoire", "LAB-X")[0] is None, "données d'un établissement non rattaché invisibles")

    print("10. Patient : mes établissements, retrait")
    r = client.get("/saas/me", headers=auth(patient_user))
    me = r.get_json()
    check(len(me["links"]) == 2 and me["is_super_admin"] is False, "deux établissements rattachés")
    r = client.delete(f"/saas/me/links/{link['id']}", headers=auth(patient_user))
    check(r.status_code == 200 and len(client.get("/saas/me/links", headers=auth(patient_user)).get_json()) == 1, "établissement retiré par le patient")

    print("11. Patient historique GNU Health (lien implicite)")
    legacy_user = services.create_user("legacy@example.com", "Jean", "Onana", "Patient")
    legacy = Patients(PatientFederationID="123456", PatientName="Jean", PatientLastname="Onana", PatientEmail="legacy@example.com", PatientPhone=null(), PatientPhone2=null(), user_id=legacy_user.id)
    db.session.add(legacy)
    db.session.commit()
    check(services.gnuhealth_ref(legacy) == "123456", "matricule GNU Health retrouvé via l'établissement historique")
    views = client.get("/saas/me/links", headers=auth(legacy_user)).get_json()
    check(views[0]["method"] == "legacy" and views[0]["establishment"] == "PDMD Santé", "lien implicite affiché")
    gnu = services.default_gnuhealth_tenant()
    r = client.get(f"/saas/admin/tenants/{gnu.id}/patients?q=onana", headers=auth(admin))
    check(r.get_json()["items"][0]["link_status"] == "active", "patient historique visible dans le back-office GNU Health")
    r = client.post(f"/saas/admin/tenants/{gnu.id}/link-tokens", json={"local_ref": "123456"}, headers=auth(admin))
    check(r.get_json()["already_linked"] is True, "pas de QR pour un patient historique déjà relié")

    print("12. Super-administrateur : comptes et journal")
    r = client.get("/saas/super/users?q=onana", headers=auth(admin))
    check(r.get_json()["total"] == 1, "recherche de comptes")
    r = client.put(f"/saas/super/users/{legacy_user.id}/active", json={"active": False}, headers=auth(admin))
    check(r.get_json()["active"] is False, "compte désactivé")
    r = client.get("/saas/me", headers=auth(legacy_user))
    check(r.status_code == 401, "compte désactivé ne peut plus appeler l'API SaaS")
    r = client.get("/saas/super/stats", headers=auth(admin))
    check(r.get_json()["tenants"] == 3 and r.get_json()["links_active"] >= 1, "statistiques globales")
    r = client.get("/saas/super/audit", headers=auth(admin))
    check(r.get_json()["total"] > 5, "journal d'audit alimenté")
    r = client.put(f"/saas/admin/tenants/{tenant['id']}/settings", json={"result_access_days": 0}, headers=auth(tenant_admin_user))
    check(r.status_code == 400, "réglage invalide refusé")
    r = client.put(f"/saas/admin/tenants/{tenant['id']}/settings", json={"result_access_days": 30, "block_unpaid_results": False}, headers=auth(tenant_admin_user))
    check(r.get_json()["settings"]["result_access_days"] == 30, "réglages de l'établissement modifiés")

    print("13. Imports de comptes rendus PDF")
    # Fonctionnalité fermée par défaut (disponible prochainement) : tout est refusé, même au super-administrateur.
    ferme = f"/saas/admin/tenants/{tenant['id']}/pdf-imports"
    r = client.get(ferme, headers=auth(tenant_admin_user))
    check(r.status_code == 403 and r.get_json()["feature"] == "pdf_import", "import PDF fermé : liste refusée")
    r = client.post(ferme, data={"file": (io.BytesIO(b"%PDF-1.4"), "a.pdf")}, headers=auth(admin), content_type="multipart/form-data")
    check(r.status_code == 403, "import PDF fermé : dépôt refusé, même au super-administrateur")
    r = client.post("/ingest/v1/pdf", data={"file": (io.BytesIO(b"%PDF-1.4"), "a.pdf")}, headers=api, content_type="multipart/form-data")
    check(r.status_code == 403 and "prochainement" in r.get_json()["message"], "import PDF fermé : dépôt par le logiciel refusé")
    app.config["ROHAFYA_PDF_IMPORT_ENABLED"] = True
    try:
        import pdfplumber  # noqa: F401
        pdf_ok = True
    except ImportError:
        pdf_ok = False
        print("  (pdfplumber absent : « pip install pdfplumber » pour tester les imports PDF)")
    if pdf_ok:
        from pathlib import Path

        fixtures = Path(__file__).resolve().parent / "fixtures"
        base = f"/saas/admin/tenants/{tenant['id']}/pdf-imports"

        def deposer(nom, headers=None, url=base, **champs):
            with open(fixtures / nom, "rb") as f:
                return client.post(url, data={"file": (f, nom), **champs}, headers=headers or auth(tenant_admin_user),
                                   content_type="multipart/form-data")

        r = deposer("labo_alpha.pdf")
        alpha = r.get_json()
        codes = {d["code"]: d for d in alpha["extraction"]["details"]}
        check(r.status_code == 201 and alpha["status"] == "pret" and len(codes) == 5, "PDF tableau : 5 valeurs, prêt à publier")
        check(alpha["local_ref"] == "CLB-0001" and alpha["validation_date"] == "2026-09-28", "dossier et date lus dans l'en-tête")
        check(codes["HB"]["warning"] and not codes["GB"]["warning"], "hors norme recalculé")
        beta = deposer("labo_beta.pdf").get_json()
        gly = next(d for d in beta["extraction"]["details"] if d["code"] == "GLY")
        check(beta["status"] == "pret" and gly["result"] == 0.936 and gly["units"] == "g/l", "PDF liste : glycémie convertie de mmol/L en g/L")
        gamma = deposer("labo_gamma.pdf").get_json()
        g_codes = {d["code"]: d for d in gamma["extraction"]["details"]}
        check(gamma["status"] == "a_relire" and any("Vitamine D" in l for l in gamma["extraction"]["a_relire"]), "analyse inconnue : à relire")
        check(g_codes["HBA1C"]["result"] == 7.4 and g_codes["HBA1C"]["warning"], "HbA1c lue (et non le « 1 » de HbA1c), hors norme")
        check(abs(g_codes["CREA"]["result"] - 9.944) < 0.001 and g_codes["CREA"]["units"] == "mg/l", "créatinine convertie de µmol/L en mg/L")

        r = client.post(f"{base}/{gamma['id']}/publier", json={}, headers=auth(tenant_admin_user))
        check(r.status_code == 409 and r.get_json()["warnings"], "publication refusée sans confirmation des points à vérifier")
        details = gamma["extraction"]["details"] + [{"name": "Vitamine D", "result": "18", "units": "ng/ml", "lower_limit": "30", "upper_limit": "100"}]
        r = client.put(f"{base}/{gamma['id']}", json={"details": details, "exam_code": "LAB-PDF-0001", "title": "Bilan GAMMA"}, headers=auth(tenant_admin_user))
        corrige = r.get_json()
        vit_d = next(d for d in corrige["extraction"]["details"] if d["name"] == "Vitamine D")
        check(corrige["status"] == "pret" and vit_d["result"] == 18.0 and vit_d["warning"], "correction manuelle : prêt, hors norme recalculé")
        client.post("/ingest/v1/patients", json=[{"local_ref": "P-88-17", "email": "aicha@example.com", "last_name": "Ngono"}], headers=api)
        r = client.post(f"{base}/{gamma['id']}/publier", json={}, headers=auth(tenant_admin_user))
        check(r.status_code == 200 and r.get_json()["status"] == "publie", "compte rendu publié")
        publie = [x for x in services.patient_records(patient, "laboratoire") if x["name"] == "LAB-PDF-0001"]
        check(len(publie) == 1 and publie[0]["test"] == "Bilan GAMMA", "résultat publié visible par le patient rattaché")
        check(client.put(f"{base}/{gamma['id']}", json={"title": "x"}, headers=auth(tenant_admin_user)).status_code == 409, "compte rendu publié non modifiable")
        r = client.get(f"{base}/{alpha['id']}/fichier", headers=auth(tenant_admin_user))
        check(r.status_code == 200 and r.data.startswith(b"%PDF"), "PDF d'origine consultable")
        r = client.post(base, data={"file": (io.BytesIO(b"pas un pdf"), "x.pdf")}, headers=auth(tenant_admin_user), content_type="multipart/form-data")
        check(r.status_code == 400, "fichier non PDF refusé")
        r = client.post(f"{base}/{alpha['id']}/reanalyse", json={"use_ai": True}, headers=auth(tenant_admin_user))
        check(r.status_code == 403, "lecture IA refusée tant que le super-admin ne l'a pas autorisée")
        r = deposer("labo_alpha.pdf", headers=api, url="/ingest/v1/pdf")
        check(r.status_code == 201 and r.get_json()["source"] == "api", "dépôt PDF par le logiciel de l'établissement (clé d'API)")
        liste = client.get(f"{base}?status=pret", headers=auth(tenant_admin_user)).get_json()
        check(liste["counts"]["publie"] == 1 and liste["total"] == 3, "liste filtrée et compteurs par statut")
        check(client.get(base, headers=auth(patient_user)).status_code == 403, "un patient n'accède pas aux imports")

        print("13 bis. Quota d'imports PDF")
        check(liste["quota"]["used"] == 4 and liste["quota"]["remaining"] == 6 and liste["quota"]["days"] == 1, "quota par défaut : 10 fichiers par jour, 4 utilisés")
        sup = f"/saas/super/tenants/{tenant['id']}"
        r = client.put(sup, json={"settings": {"pdf_quota_files": 0}}, headers=auth(admin))
        check(r.status_code == 400, "quota invalide refusé")
        r = client.put(sup, json={"settings": {"pdf_quota_files": "4"}}, headers=auth(admin))
        check(r.status_code == 200 and r.get_json()["settings"]["pdf_quota_files"] == 4, "quota réglé par le super-administrateur")
        client.put(f"/saas/admin/tenants/{tenant['id']}/settings", json={"pdf_quota_files": 999, "pdf_quota_days": 9}, headers=auth(tenant_admin_user))
        reglages = client.get(f"/saas/admin/tenants/{tenant['id']}", headers=auth(tenant_admin_user)).get_json()["settings"]
        check(reglages["pdf_quota_files"] == 4 and reglages["pdf_quota_days"] == 1, "quota visible mais non modifiable par l'administrateur")
        r = deposer("labo_beta.pdf")
        demain = (services.now().date() + datetime.timedelta(days=1)).isoformat()
        check(r.status_code == 429 and r.get_json()["quota"]["next_available_at"].startswith(demain), "5e fichier refusé : reprise le lendemain")
        r = deposer("labo_beta.pdf", headers=api, url="/ingest/v1/pdf")
        check(r.status_code == 429, "le quota s'applique aussi aux dépôts par le logiciel")
        client.delete(f"{base}/{alpha['id']}", headers=auth(tenant_admin_user))
        check(deposer("labo_beta.pdf").status_code == 429, "supprimer un compte rendu ne rend pas de crédit")
        client.put(sup, json={"settings": {"pdf_quota_files": 5, "pdf_quota_days": 3}}, headers=auth(admin))
        r = deposer("labo_beta.pdf")
        q = client.get(base, headers=auth(tenant_admin_user)).get_json()["quota"]
        check(r.status_code == 201 and q["used"] == 5 and q["remaining"] == 0, "quota relevé : import accepté")
        gnu = services.default_gnuhealth_tenant()
        r = deposer("labo_beta.pdf", headers=auth(admin), url=f"/saas/admin/tenants/{gnu.id}/pdf-imports")
        check(r.status_code == 400, "import PDF indisponible pour l'établissement GNU Health")
        r = client.post("/saas/super/tenants", json={"name": "Labo Scan", "source_type": "pdf"}, headers=auth(admin))
        check(r.status_code == 201 and r.get_json()["source_type"] == "pdf", "établissement créé avec la méthode « scan / PDF »")

    print("14. Suppression d'un établissement")
    gnu = services.default_gnuhealth_tenant()
    check(client.delete(f"/saas/super/tenants/{gnu.id}", json={"confirm_slug": gnu.slug}, headers=auth(admin)).status_code == 400, "établissement GNU Health non supprimable")
    r = client.delete(f"/saas/super/tenants/{tenant['id']}", json={"confirm_slug": "mauvais"}, headers=auth(admin))
    check(r.status_code == 400, "confirmation par identifiant exigée")
    check(client.delete(f"/saas/super/tenants/{tenant['id']}", json={"confirm_slug": tenant["slug"]}, headers=auth(tenant_admin_user)).status_code == 403, "un admin d'établissement ne peut pas supprimer")
    r = client.delete(f"/saas/super/tenants/{tenant['id']}", json={"confirm_slug": tenant["slug"]}, headers=auth(admin))
    deleted = r.get_json()["deleted"]
    check(r.status_code == 200 and deleted["donnees"] >= 5 and deleted["administrateurs"] == 1, "établissement supprimé avec ses données")
    check(client.get(f"/saas/super/tenants/{tenant['id']}", headers=auth(admin)).status_code == 404, "établissement introuvable ensuite")
    check(services.patient_records(patient, "laboratoire") == [], "ses résultats ne sont plus visibles du patient")
    check(db.session.get(User, patient_user.id) is not None, "le compte du patient est conservé")
    db.session.expire_all()
    check("EstablishmentAdmin" not in services.role_names(db.session.get(User, tenant_admin_user.id)), "rôle d'administrateur retiré")
    journal = client.get("/saas/super/audit", headers=auth(admin)).get_json()["items"]
    check(journal[0]["action"] == "tenant.deleted", "suppression inscrite au journal")

    print("15. flask saas init : permissions par défaut des rôles Patient et Doctor")
    from Rohafya.accounts.models import Permissions, Role  # noqa: E402
    from Rohafya.permissions.permissions import PERMISSIONS  # noqa: E402
    from Rohafya.saas.cli import saas_cli  # noqa: E402

    def codes(tree, parent=""):
        for key, value in tree.items():
            code = f"{parent}.{key}" if parent else key
            yield from codes(value, code) if isinstance(value, dict) else [code]

    catalogue = list(codes(PERMISSIONS))
    nb_patients = sum(code.startswith("patients.") for code in catalogue)
    nb_doctors = sum(code.startswith("doctors.") for code in catalogue)
    app.cli.add_command(saas_cli)
    runner = app.test_cli_runner()
    lab = "patients.patients_laboratoire.all_results"
    check(not db.session.get(User, patient_user.id).has_permission(lab), "avant : rôle Patient sans permission (403 partout)")
    sortie = runner.invoke(args=["saas", "init"]).output
    db.session.expire_all()
    patient_role = db.session.execute(select(Role).filter_by(name="Patient")).scalar_one()
    doctor_role = db.session.execute(select(Role).filter_by(name="Doctor")).scalar_one()
    check(f"Permissions : {len(catalogue)} créée(s)." in sortie, "catalogue des permissions créé en base")
    check(len(patient_role.permissions) == nb_patients and len(doctor_role.permissions) == nb_doctors,
          "Patient reçoit patients.*, Doctor reçoit doctors.*")
    check(all(p.code.startswith("patients.") for p in patient_role.permissions), "aucune permission d'administration donnée aux patients")
    check(db.session.get(User, patient_user.id).has_permission(lab), "après : le patient a accès à ses résultats")
    sortie = runner.invoke(args=["saas", "init"]).output
    check("0 créée(s)" in sortie and "Rôle Patient : 0 permission(s)" in sortie, "relancer init ne change rien (idempotent)")
    extra = db.session.execute(select(Permissions).filter(Permissions.code.startswith("administration."))).scalars().first()
    retiree = next(p for p in patient_role.permissions if p.code == lab)
    patient_role.permissions.append(extra)
    patient_role.permissions.remove(retiree)
    db.session.commit()
    runner.invoke(args=["saas", "init", "--sans-permissions"])
    db.session.expire_all()
    codes_patient = {p.code for p in db.session.execute(select(Role).filter_by(name="Patient")).scalar_one().permissions}
    check(extra.code in codes_patient and lab not in codes_patient, "--sans-permissions : réglages manuels intacts")
    runner.invoke(args=["saas", "init"])
    db.session.expire_all()
    codes_patient = {p.code for p in db.session.execute(select(Role).filter_by(name="Patient")).scalar_one().permissions}
    check(extra.code in codes_patient and lab in codes_patient, "init : permission manquante rajoutée, ajout manuel conservé")

print(f"\n{ok_count} vérifications réussies.")
