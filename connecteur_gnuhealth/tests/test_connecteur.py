"""Test de bout en bout du connecteur : une fausse base GNU Health envoie ses données
à la vraie API d'ingestion ROHAFYA (Flask, SQLite en mémoire).

Lancement, depuis la racine du dépôt :
    Rohafya/envDoc/bin/python connecteur_gnuhealth/tests/test_connecteur.py
"""
import datetime
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as Obj

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flask import Flask
from sqlalchemy import JSON, select

import Rohafya
from Rohafya import db, jwt, login_manager

app = Flask("Rohafya", root_path=os.path.dirname(Rohafya.__file__))
app.config.update(
    SQLALCHEMY_DATABASE_URI="sqlite://",
    SECRET_KEY="test-secret",
    JWT_SECRET_KEY="test-jwt",
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

from Rohafya.accounts.models import User, UserActivity  # noqa: E402
from Rohafya.custom.customization import CustomUserManager  # noqa: E402
from Rohafya.saas import services  # noqa: E402
from Rohafya.saas.constants import LINK_ACTIVE  # noqa: E402
from Rohafya.saas.ingest_routes import ingest  # noqa: E402
from Rohafya.saas.models import PatientLink, Tenant, TenantPatient, TenantRecord  # noqa: E402

CustomUserManager(app, db, User)
app.register_blueprint(ingest)
UserActivity.__table__.c.route.type = JSON()

from connecteur_gnuhealth import conversion  # noqa: E402
from connecteur_gnuhealth.config import Configuration, ErreurConfiguration  # noqa: E402
from connecteur_gnuhealth.gnuhealth import SourceGnuHealth  # noqa: E402
from connecteur_gnuhealth.rohafya import ClientRohafya, ErreurRohafya  # noqa: E402
from connecteur_gnuhealth.synchro import Etat, Verrou, synchroniser  # noqa: E402

ok_count = 0


def check(condition, label):
    global ok_count
    if not condition:
        raise AssertionError(label)
    ok_count += 1
    print("  ✔", label)


# =====================================================================
# Fausse base GNU Health (mêmes champs que les modèles Tryton lus par l'API)
# =====================================================================

T0 = datetime.datetime(2026, 9, 1, 8, 0, 0)
T1 = datetime.datetime(2026, 10, 1, 8, 0, 0)


def personne(prenom, nom, matricule=None):
    return Obj(name=Obj(name=prenom, lastname=nom, federation_account=matricule))


def party(pid, matricule, prenom, nom, email=None, result_online=True, write_date=None):
    return Obj(id=pid, federation_account=matricule, name=prenom, lastname=nom, email=email, phone="690000000",
               dob=datetime.date(1990, 5, 17), gender="f", result_online=result_online,
               create_date=T0, write_date=write_date)


class FausseSource(object):
    """Même interface que SourceGnuHealth, sur des listes en mémoire."""

    def __init__(self):
        self.parties = []
        self.examens_par_type = {"laboratoire": [], "imagerie": [], "exploration": []}
        self.invoices = []

    @staticmethod
    def _modifie(obj, depuis):
        return depuis is None or (obj.write_date or obj.create_date) >= depuis

    def matricules_patients(self):
        return {p.id: p.federation_account for p in self.parties if p.result_online and p.federation_account}

    def patients(self, depuis=None):
        return [p for p in self.parties if p.result_online and p.federation_account and self._modifie(p, depuis)]

    def examens(self, type_examen, ids_parties, depuis=None, ids_complets=()):
        return [e for e in self.examens_par_type[type_examen]
                if e.patient.name.id in ids_parties and (self._modifie(e, depuis) or e.patient.name.id in ids_complets)]

    def prescripteur(self, type_examen, examen):
        return "Dr Paul Mbarga" if getattr(examen, "request_order", None) or getattr(examen, "order", None) else None

    def factures(self, ids_parties, depuis=None, ids_complets=()):
        return [i for i in self.invoices if i.party.id in ids_parties and i.number
                and (self._modifie(i, depuis) or i.state == "posted" or i.party.id in ids_complets)]

    def numeros_factures(self, ids_parties):
        resultat = {}
        for i in self.invoices:
            if i.party.id in ids_parties:
                resultat.setdefault(i.party.id, []).append((i.number, i.reference))
        return resultat

    def lignes_facture(self, invoice):
        return invoice.lines


def patient_gnu(p):
    return Obj(name=Obj(id=p.id, name=p.name, lastname=p.lastname))


def examen_labo(nom, p, write_date=None):
    critere = Obj(code="HB", create_date=T0, gnuhealth_lab_id=Obj(request_order=77), id=1, lower_limit=Decimal("12"),
                  name="Hémoglobine", normal_range="12 - 16", rec_name="Hémoglobine", remarks=None,
                  result=Decimal("13.5"), result_text=None, sequence=1, test_type_id=Obj(name="NFS"),
                  units=Obj(name="g/dL"), upper_limit=Decimal("16"), warning=False)
    return Obj(id=hash(nom) % 1000, name=nom, rec_name=nom, patient=patient_gnu(p), test=Obj(name="NFS"),
               analytes_summary="Hb 13.5", date_analysis=T0, date_requested=T0, diagnosis=None,
               done_by=personne("Jean", "Biologiste"), done_date=T0, historize=False, macroscopie=None,
               microscopie=None, renseignements=None, request_order=77, results="Normal", serializer=None,
               serializer_current=None, state="validated", validated_by=personne("Marie", "Valideur", "MAT-BIO-1"),
               validation_date=T0, critearea=[critere], qr=b"\x89PNG", create_date=T0, write_date=write_date)


def examen_imagerie(numero, p):
    etude = Obj(create_date=T0, create_uid=Obj(name="admin"), date=T0, description="Thorax", id=9, ident="ST1",
                imaging_test=Obj(number=numero, order="ORD-1"), instance_uid="1.2.3", institution="PDMD",
                link="https://orthanc/1", merge_id=None, patient=Obj(name="ORTH-1", rec_name="ORTH-1", patient=None),
                rec_name="ST1", ref_phys=None, req_phys=None, requested_procedure_id=None,
                server=Obj(rec_name="orthanc", domain="orthanc.local", user="lecteur"), uuid="u-1", write_date=T0,
                write_uid=Obj(name="admin"))
    return Obj(id=5, number=numero, rec_name=numero, patient=patient_gnu(p), date=T0, request_date=T0, create_date=T0,
               computed_age="36a", create_uid=Obj(name="admin"), done_by=None, done_date=T0, doctor=None,
               conclusion="RAS", indication="Toux", merge_id=None, order="ORD-1", realisateur=None,
               request=Obj(request=55, service=Obj(name="Radiologie")), requested_test=Obj(name="Radio thorax"),
               resultat="Normal", serializer=None, serializer_current=None, state="done", technique="Face",
               validated_by=None, validation_date=T0, studies=[etude], write_date=None)


def facture(numero, p, state, a_payer, reference=None, write_date=None):
    ligne = Obj(product=Obj(name="NFS"), quantity=1.0)
    return Obj(number=numero, reference=reference, party=Obj(id=p.id), invoice_date=T0.date(), state=state,
               amount_to_pay=Decimal(a_payer), montant_assurance=Decimal("0"), montant_patient=Decimal("10000"),
               untaxed_amount=Decimal("10000"), amount_to_pay_today=Decimal(a_payer), total_amount2=Decimal("10000"),
               lines=[ligne], create_date=T0, write_date=write_date)


def transport_flask(client, cle):
    def envoyer(methode, chemin, corps=None):
        reponse = client.open(chemin, method=methode, json=corps, headers={"X-ROHAFYA-API-Key": cle})
        return reponse.status_code, reponse.get_json() or {}
    return envoyer


# =====================================================================
# Scénario
# =====================================================================

with app.app_context():
    db.create_all()
    flask_client = app.test_client()
    cle, empreinte, indice = services.generate_api_key()
    tenant = Tenant(slug="clinique-gnu", name="Clinique GNU", source_type="api", api_key_hash=empreinte, api_key_hint=indice,
                    settings={"block_unpaid_results": True})
    db.session.add(tenant)
    db.session.commit()
    attentes = []
    client = ClientRohafya(transport_flask(flask_client, cle), taille_lot=2, attente=attentes.append)
    dossier = tempfile.mkdtemp()
    etat = Etat(os.path.join(dossier, "etat.json"))

    aicha = party(1, "MAT-001", "Aïcha", "Ngono", email="aicha@example.com")
    paul = party(2, "MAT-002", "Paul", "Essomba")
    refus = party(3, "MAT-003", "Sans", "Accord", result_online=False)
    source = FausseSource()
    source.parties = [aicha, paul, refus]
    source.examens_par_type["laboratoire"] = [examen_labo("LAB-1", aicha), examen_labo("LAB-2", paul), examen_labo("LAB-3", refus)]
    source.examens_par_type["imagerie"] = [examen_imagerie("IMG-1", aicha)]
    source.invoices = [
        facture("FAC-1", aicha, "posted", "9000"),             # impayée : bloque les détails
        facture("FAC-2", aicha, "paid", "0"),
        facture("FAC-3", paul, "paid", "0"),
        facture("AV-1", paul, "paid", "0", reference="FAC-3"),  # avoir : FAC-3 et AV-1 écartés
        facture("FAC-9", refus, "posted", "5000"),
    ]

    print("1. Conversion au format ROHAFYA")
    lab = conversion.laboratoire(source.examens_par_type["laboratoire"][0], "MAT-001", "Dr Paul Mbarga")
    check(lab["name"] == "LAB-1" and lab["local_ref"] == "MAT-001" and lab["test"] == "NFS", "laboratoire : code, dossier, test")
    check(lab["validation_date"] == "2026-09-01T08:00:00" and lab["validated_by"] == "Marie Valideur", "dates ISO et noms complets")
    check(lab["details"][0]["result"] == 13.5 and lab["details"][0]["units"] == "g/dL", "critères : décimaux en nombres, unités")
    check("qr" not in lab and "id" not in lab and "expiration_date" not in lab, "champs calculés par ROHAFYA non envoyés")
    stock = conversion.laboratoire(Obj(name="LAB-X", patient=None), "MAT-001")
    check(stock["name"] == "LAB-X" and stock["macroscopie"] is None and stock["details"] == [], "GNU Health sans champs PDMD : pas d'erreur")
    check(conversion.facture(source.invoices[0], "MAT-001", source.invoices[0].lines)["reference"] == "FAC-1", "facture : code = numéro")
    annules, avoirs = conversion.factures_annulees([("FAC-3", None), ("AV-1", "FAC-3"), ("FAC-4", "texte libre")])
    check(annules == {"FAC-3", "AV-1"} and avoirs == {"AV-1": "FAC-3"}, "avoir : facture et avoir écartés, référence libre ignorée")

    print("2. Première synchronisation (complète)")
    check(client.ping()["slug"] == "clinique-gnu", "clé d'API reconnue")
    essai = synchroniser(source, client, etat, essai=True, maintenant=T1)
    check(essai["laboratoire"] == {"a_envoyer": 2} and etat.derniere_synchro is None, "--essai : rien d'envoyé ni d'enregistré")
    check(db.session.execute(select(TenantRecord)).first() is None, "--essai : base ROHAFYA vide")
    bilan = synchroniser(source, client, etat, maintenant=T1)
    patients = {p.local_ref: p for p in db.session.execute(select(TenantPatient)).scalars()}
    check(set(patients) == {"MAT-001", "MAT-002"}, "patients sans accord (result_online) non envoyés")
    check(patients["MAT-001"].email == "aicha@example.com", "e-mail du patient transmis (rattachement automatique)")
    codes = {(r.kind, r.code) for r in db.session.execute(select(TenantRecord)).scalars()}
    check(("laboratoire", "LAB-1") in codes and ("laboratoire", "LAB-2") in codes and ("laboratoire", "LAB-3") not in codes,
          "résultats de laboratoire des seuls patients autorisés")
    check(("imagerie", "IMG-1") in codes, "imagerie envoyée")
    check({c for k, c in codes if k == "facture"} == {"FAC-1", "FAC-2"}, "factures annulées par avoir et avoirs écartés")
    check(bilan["laboratoire"]["created"] == 2 and attentes == [], "envoi par lots de 2, sans nouvelle tentative")
    check(etat.derniere_synchro == T1 - datetime.timedelta(minutes=10), "date de synchronisation enregistrée (avec chevauchement)")

    print("3. Ce que voit le patient rattaché")
    user = services.create_user("aicha@example.com", "Aïcha", "Ngono", "Patient")
    profil = services.ensure_patient_profile(user)
    db.session.add(PatientLink(patient_id=profil.id, tenant_id=tenant.id, local_ref="MAT-001", status=LINK_ACTIVE))
    db.session.commit()
    resultats = services.patient_records(profil, "laboratoire")
    check(len(resultats) == 1 and resultats[0]["establishment"] == "Clinique GNU" and resultats[0]["requestor"] == "Dr Paul Mbarga",
          "liste des résultats au format de l'API")
    record, record_tenant = services.find_patient_record(profil, "laboratoire", "LAB-1")
    corps, statut = services.record_details_response(record, record_tenant)
    check(statut == 403, "détails bloqués : facture FAC-1 impayée (même règle que GNU Health)")
    corps, statut = services.record_details_response(record, record_tenant, check_unpaid=False)
    check(statut == 200 and corps[0]["name"] == "Hémoglobine", "détails = critères du résultat")
    produits = services.find_patient_record(profil, "facture", "FAC-1")[0].details
    check(produits == [{"product_name": "NFS", "quantity": 1.0}], "lignes de facture")

    print("4. Synchronisation incrémentale")
    T2 = T1 + datetime.timedelta(hours=1)
    source.examens_par_type["laboratoire"][0].results = "Corrigé"
    source.examens_par_type["laboratoire"][0].write_date = T1 + datetime.timedelta(minutes=30)
    source.invoices[0].state = "paid"
    source.invoices[0].amount_to_pay_today = Decimal("0")
    source.invoices[0].write_date = T1 + datetime.timedelta(minutes=40)
    source.invoices.append(facture("AV-2", aicha, "paid", "0", reference="FAC-2", write_date=T1 + datetime.timedelta(minutes=45)))
    bilan = synchroniser(source, client, etat, maintenant=T2)
    check(bilan["patients"]["total"] == 0 and bilan["laboratoire"] == {"created": 0, "updated": 1, "total": 1},
          "seul le résultat modifié est renvoyé")
    check(services.find_patient_record(profil, "laboratoire", "LAB-1")[0].payload["results"] == "Corrigé", "correction reçue")
    check(bilan["factures_retirees"] == 1 and services.find_patient_record(profil, "facture", "FAC-2")[0] is None,
          "avoir créé après coup : facture annulée retirée de ROHAFYA")
    check(services.record_details_response(record, record_tenant)[1] == 200, "facture réglée : détails débloqués")

    print("5. Patient qui donne son accord après coup")
    refus.result_online = True
    refus.write_date = T2 + datetime.timedelta(minutes=5)
    bilan = synchroniser(source, client, etat, maintenant=T2 + datetime.timedelta(hours=1))
    codes = {(r.kind, r.code) for r in db.session.execute(select(TenantRecord)).scalars()}
    check(("laboratoire", "LAB-3") in codes and ("facture", "FAC-9") in codes, "tout son historique est envoyé")

    print("6. Pannes et erreurs")
    pannes = [503, 503]

    def transport_instable(methode, chemin, corps=None):
        if pannes:
            return pannes.pop(), {"message": "indisponible"}
        return transport_flask(flask_client, cle)(methode, chemin, corps)

    attentes[:] = []
    instable = ClientRohafya(transport_instable, attente=attentes.append)
    check(instable.ping()["slug"] == "clinique-gnu" and attentes == [2, 4], "erreur 503 : nouvelles tentatives (2 s, 4 s)")
    avant = etat.derniere_synchro
    mauvais = ClientRohafya(transport_flask(flask_client, "rohafya_faux"), attente=attentes.append)
    try:
        synchroniser(source, mauvais, etat, complet=True, maintenant=T2 + datetime.timedelta(days=1))
        check(False, "une clé refusée doit arrêter la synchronisation")
    except ErreurRohafya as erreur:
        check(erreur.statut == 401, "clé refusée : arrêt immédiat (pas de nouvelle tentative)")
    check(etat.derniere_synchro == avant, "échec : la date de synchronisation n'avance pas")
    check(client.supprimer("factures", "INCONNUE/2026") is False, "retrait d'une facture jamais envoyée : ignoré")

    print("7. Rattachement par QR code")
    tenant.settings = {"block_unpaid_results": True, "link_token_days": 30}
    db.session.commit()
    jeton = client.jeton_lien("MAT-002", qr=True)
    check(jeton["url"].startswith("https://rohafya.com/l/") and jeton["qr_png"] and len(jeton["short_code"]) == 8,
          "jeton de rattachement avec QR code")
    check(client.jeton_lien("MAT-001")["already_linked"], "dossier déjà rattaché signalé")

print("8. Configuration, verrou, requêtes Tryton")
fichier = os.path.join(dossier, "connecteur.ini")
with open(fichier, "w") as f:
    f.write("[rohafya]\nurl = http://api.exemple.com\ncle_api = x\n[gnuhealth]\ntrytond_conf = /t.conf\nbase = b\n")
try:
    Configuration(fichier)
    check(False, "une URL http doit être refusée")
except ErreurConfiguration:
    check(True, "URL non chiffrée refusée")
with open(fichier, "w") as f:
    f.write("[rohafya]\nurl = https://api.exemple.com\n[gnuhealth]\ntrytond_conf = /t.conf\nbase = b\nfiltre_result_online = non\n")
os.environ["ROHAFYA_CLE_API"] = "rohafya_env"
config = Configuration(fichier)
check(config.cle_api == "rohafya_env" and config.filtre_result_online is False and config.taille_lot == 100,
      "clé lue dans l'environnement, valeurs par défaut")
with Verrou(os.path.join(dossier, "etat.lock")):
    try:
        with Verrou(os.path.join(dossier, "etat.lock")):
            check(False, "deux synchronisations ne doivent pas tourner en même temps")
    except RuntimeError:
        check(True, "une synchronisation déjà en cours bloque la suivante")


class ModeleEspion(object):
    def __init__(self):
        self.domaines = []

    def search(self, domaine, **kwargs):
        self.domaines.append(domaine)
        return []


espion = ModeleEspion()
gnu = SourceGnuHealth("/t.conf", "b")
gnu.pool = Obj(get=lambda nom: espion)
gnu.examens("laboratoire", [1, 2], T1, {2})
check(espion.domaines[-1] == [("patient.name", "in", [1, 2]),
                              ["OR", ("write_date", ">=", T1), ("create_date", ">=", T1), ("patient.name", "in", [2])]],
      "domaine Tryton des résultats : modifiés depuis, ou patient à envoyer en entier")
gnu.factures([1], T1)
check(espion.domaines[-1][-1] == ["OR", ("write_date", ">=", T1), ("create_date", ">=", T1), ("state", "=", "posted")],
      "domaine Tryton des factures : les factures non soldées sont toujours renvoyées")
check(gnu.examens("laboratoire", [], None) == [], "aucun patient autorisé : aucune requête")
try:
    with SourceGnuHealth(os.path.join(dossier, "absent.conf"), "b"):
        pass
    check(False, "un trytond.conf illisible doit être signalé")
except Exception as erreur:
    check("compte" in str(erreur) and "README" in str(erreur), "trytond.conf illisible : message clair (mauvais compte système)")

print("\n{} vérifications réussies.".format(ok_count))
