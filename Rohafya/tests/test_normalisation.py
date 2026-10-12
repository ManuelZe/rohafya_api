"""Les trois voies d'entrée (connecteur GNU Health, API ROHAFYA, HL7 FHIR R4) donnent les mêmes champs.

Lancement :
    Rohafya/envDoc/bin/python Rohafya/tests/test_normalisation.py

Les champs attendus sont ceux des routes GNU Health du patient, donc des interfaces du front
(src/app/patients/patients.models.ts).
"""
import sys
from pathlib import Path
from types import SimpleNamespace as Obj

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from connecteur_gnuhealth import conversion  # noqa: E402
from Rohafya.saas.fhir import BundleIndex, map_diagnostic_report  # noqa: E402
from Rohafya.saas.normalization import RESULT_FIELDS, normalize_details, normalize_payload  # noqa: E402

failures = 0


def check(condition, label):
    global failures
    print(("  ✔ " if condition else "  ✘ ") + label)
    if not condition:
        failures += 1


# Champs des interfaces du front, hors champs calculés à l'affichage.
COMPUTED = {"id", "establishment", "expiration_date", "nbr_days_before_expiration", "statut_expiration", "error"}
FRONT = {
    "laboratoire": {
        "analytes_summary", "date_analysis", "date_requested", "diagnosis", "done_by", "done_date", "historize",
        "macroscopie", "matricule_patient", "microscopie", "name", "patient", "qr", "rec_name", "renseignements",
        "request_order", "requestor", "results", "serializer", "serializer_current", "state", "test",
        "validated_by", "validated_fed_id", "validation_date",
    },
    "imagerie": {
        "computed_age", "conclusion", "create_date", "create_uid", "date", "doctor", "done_by", "done_date",
        "indication", "merge_id", "number", "order", "patient", "realisateur", "rec_name", "request_date",
        "request_order", "requested_test", "requestor", "resultat", "serializer", "serializer_current",
        "service_cot", "state", "technique", "validated_by", "validated_fed_id", "validation_date",
    },
    "exploration": {
        "analytes_summary", "commentaire", "date_analysis", "date_requested", "diagnosis", "done_by", "done_date",
        "historize", "indication", "matricule_patient", "name", "pathologist", "patient", "realisateur", "rec_name",
        "request_order", "requestor", "resultat", "results", "serializer", "state", "technique", "test",
        "traitement", "validated_by", "validated_fed_id", "validation_date",
    },
}


def stored(kind, data):
    """Ce que upsert_record enregistre (hors local_ref, details et products)."""
    raw = {k: v for k, v in data.items() if k not in ("local_ref", "details", "products")}
    return normalize_payload(kind, raw)


print("1. Champs de référence = interfaces du front")
for kind, fields in FRONT.items():
    check(set(RESULT_FIELDS[kind]) == fields, f"{kind} : mêmes champs que patients.models.ts")

print("2. Connecteur GNU Health")
personne = Obj(name=Obj(name="Martin", lastname="OWONA", federation_account="BIO-1"))
lab_gnu = Obj(
    name="LAB-1", rec_name="LAB-1", test=Obj(name="NFS"), results="Hémoglobine abaissée", diagnosis="Anémie",
    validated_by=personne, validation_date=None, critearea=[
        Obj(name="Hémoglobine", result=11.4, units=Obj(name="g/dl"), lower_limit=12, upper_limit=16, normal_range="12 – 16",
            remarks="Bas", warning=True, code="HB", sequence=1, id=1, rec_name="Hémoglobine", result_text="",
            test_type_id=Obj(name="NFS"), create_date=None, gnuhealth_lab_id=Obj(request_order=7)),
    ],
)
connecteur = conversion.laboratoire(lab_gnu, "P-1", "Dr Paul")
lab_connecteur = stored("laboratoire", connecteur)
check(set(lab_connecteur) == FRONT["laboratoire"], "laboratoire : tous les champs, rien de plus")
check(lab_connecteur["results"] == "Hémoglobine abaissée" and lab_connecteur["qr"] is None, "résultat dans « results », qr présent (vide)")
details = normalize_details("laboratoire", connecteur["details"])
check(details[0]["units"] == "g/dl" and details[0]["warning"] is True, "valeurs mesurées conservées")

imagerie_gnu = Obj(number="IMG-1", resultat="Foie normal", conclusion="Normal", studies=[
    Obj(description="Écho", link="https://pacs/1", server=Obj(rec_name="PACS", domain="pacs", user="admin"))
])
img = conversion.imagerie(imagerie_gnu, "P-1")
check("server_username" not in img["details"][0], "imagerie : identifiants du serveur d'images non envoyés")
check(set(stored("imagerie", img)) == FRONT["imagerie"], "imagerie : tous les champs, rien de plus")

print("3. API ROHAFYA (champs partiels, synonymes, champs inconnus)")
api = stored("exploration", {"name": "EXP-1", "test": "ECG", "resultats": "Rythme sinusal", "conclusion": "ECG normal", "champ_inconnu": "x"})
check(set(api) == FRONT["exploration"], "exploration : tous les champs, champ inconnu écarté")
check(api["resultat"] == "Rythme sinusal" and api["diagnosis"] == "ECG normal" and api["rec_name"] == "EXP-1", "synonymes ramenés (resultats → resultat, conclusion → diagnosis)")
api_img = stored("imagerie", {"number": "IMG-2", "results": "Foie normal", "conclusion": "Normal"})
check(api_img["resultat"] == "Foie normal", "imagerie : « results » ramené à « resultat »")
api_lab = stored("laboratoire", {"name": "LAB-2", "resultat": "Glycémie normale", "results": "Texte prioritaire"})
check(api_lab["results"] == "Texte prioritaire", "un champ fourni n'est pas écrasé par son synonyme")
mesures = normalize_details("laboratoire", [{"name": "Glycémie", "value": 0.9, "unit": "g/l", "reference_range": "0,7 – 1,1", "server": "x"}])
check(mesures[0]["result"] == 0.9 and mesures[0]["units"] == "g/l" and mesures[0]["normal_range"] == "0,7 – 1,1" and "server" not in mesures[0],
      "lignes de détail : synonymes ramenés, champ inconnu écarté")
check(set(mesures[0]) == set(normalize_details("laboratoire", connecteur["details"])[0]), "lignes de détail : mêmes clés que le connecteur")

print("4. HL7 FHIR R4")
entries = [
    {"fullUrl": "urn:uuid:o1", "resource": {"resourceType": "Observation", "id": "o1", "code": {"text": "Glycémie"},
                                          "valueQuantity": {"value": 1.4, "unit": "g/l"}, "referenceRange": [{"low": {"value": 0.7}, "high": {"value": 1.1}}],
                                          "interpretation": [{"coding": [{"code": "H"}]}]}},
    {"resource": {"resourceType": "DiagnosticReport", "id": "dr1", "identifier": [{"value": "LAB-3"}], "category": [{"coding": [{"code": "LAB"}]}],
                  "code": {"text": "Glycémie à jeun"}, "subject": {"reference": "Patient/p1"}, "result": [{"reference": "urn:uuid:o1"}],
                  "conclusion": "Hyperglycémie", "text": {"div": "<div><p>Glycémie à jeun élevée.</p><p>À recontrôler.</p></div>"}}},
    {"resource": {"resourceType": "DiagnosticReport", "id": "dr2", "identifier": [{"value": "IMG-3"}], "category": [{"coding": [{"code": "RAD"}]}],
                  "code": {"text": "Radio thorax"}, "subject": {"reference": "Patient/p1"}, "conclusion": "Normal"}},
]
index = BundleIndex(entries)
kind, data = map_diagnostic_report(entries[1]["resource"], index)
fhir_lab = stored(kind, data)
check(kind == "laboratoire" and set(fhir_lab) == FRONT["laboratoire"], "laboratoire : tous les champs, rien de plus")
check(fhir_lab["diagnosis"] == "Hyperglycémie" and fhir_lab["results"] == "Glycémie à jeun élevée.\nÀ recontrôler.", "conclusion → diagnosis, texte du compte rendu → results")
fhir_details = normalize_details(kind, data["details"])
check(fhir_details[0]["warning"] is True and fhir_details[0]["lower_limit"] == 0.7, "Observation → valeur hors norme")
check(set(fhir_details[0]) == set(normalize_details("laboratoire", connecteur["details"])[0]), "lignes de détail : mêmes clés que le connecteur")
kind, data = map_diagnostic_report(entries[2]["resource"], index)
fhir_img = stored(kind, data)
check(set(fhir_img) == FRONT["imagerie"] and fhir_img["conclusion"] == "Normal" and fhir_img["resultat"] is None,
      "imagerie : conclusion distincte du résultat (plus de doublon)")

print("5. Mêmes clés quelle que soit la voie")
check(set(lab_connecteur) == set(api_lab) == set(fhir_lab), "laboratoire : connecteur = API = FHIR")
check(set(stored("imagerie", img)) == set(api_img) == set(fhir_img), "imagerie : connecteur = API = FHIR")

if failures:
    print(f"\n{failures} échec(s)")
    sys.exit(1)
print("\nTous les contrôles sont passés.")
