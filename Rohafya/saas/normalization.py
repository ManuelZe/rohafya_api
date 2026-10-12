"""Forme unique des résultats et factures, quelle que soit leur voie d'entrée.

Les trois voies d'entrée — connecteur GNU Health, API ROHAFYA (/ingest/v1) et HL7 FHIR R4 —
enregistrent leurs données par `ingestion.upsert_record`, qui les ramène ici aux champs que
renvoient les routes GNU Health du patient (patients/laboratoire, patients/imagerie,
patients/exploration, patients/charge_patients). Ce sont les champs des interfaces du front
(src/app/patients/patients.models.ts : ExamensLab, ExamenImagerie, ExplorationLab,
FacturesResponseApi).

- Un champ absent vaut None : le front reçoit toujours les mêmes clés.
- Les champs inconnus sont écartés (pas de fuite de données non prévues, ex. identifiants de serveur).
- Quelques synonymes courants sont acceptés à l'entrée (« resultats » → « resultat »…).
- Les champs calculés à l'affichage (id, establishment, expiration_date, nbr_days_before_expiration,
  statut_expiration, error) ne sont pas stockés : `services.serialize_record` les ajoute.

La normalisation est aussi appliquée à la lecture, pour les enregistrements reçus avant elle.
"""
from .constants import CODE_FIELD, KIND_EXPLORATION, KIND_IMAGING, KIND_INVOICE, KIND_LAB

# Champs stockés, dans l'ordre des routes GNU Health.
RESULT_FIELDS = {
    KIND_LAB: (
        "analytes_summary", "date_analysis", "date_requested", "diagnosis", "done_by", "done_date",
        "historize", "macroscopie", "microscopie", "name", "patient", "matricule_patient", "qr",
        "rec_name", "renseignements", "request_order", "requestor", "results", "serializer",
        "serializer_current", "state", "test", "validated_by", "validated_fed_id", "validation_date",
    ),
    KIND_IMAGING: (
        "date", "request_date", "create_date", "computed_age", "create_uid", "done_by", "done_date",
        "doctor", "conclusion", "indication", "merge_id", "number", "order", "rec_name", "patient",
        "realisateur", "request_order", "service_cot", "requested_test", "requestor", "resultat",
        "serializer", "serializer_current", "state", "technique", "validated_by", "validated_fed_id",
        "validation_date",
    ),
    KIND_EXPLORATION: (
        "analytes_summary", "commentaire", "date_analysis", "date_requested", "diagnosis", "done_by",
        "done_date", "indication", "historize", "name", "pathologist", "patient", "matricule_patient",
        "rec_name", "request_order", "requestor", "realisateur", "results", "resultat", "serializer",
        "state", "test", "technique", "traitement", "validated_by", "validated_fed_id", "validation_date",
    ),
    KIND_INVOICE: (
        "invoice_number", "date", "amount_to_pay", "montant_assurance", "montant_patient", "reference",
        "untaxed_amount", "amount_to_pay_today", "state", "total_amount2",
    ),
}

# Champ du résultat rédigé : « results » en laboratoire, « resultat » en imagerie,
# « resultat » et « results » en exploration fonctionnelle.
ALIASES = {
    KIND_LAB: {"resultat": "results", "resultats": "results", "result": "results", "conclusion": "diagnosis"},
    KIND_IMAGING: {"results": "resultat", "resultats": "resultat", "result": "resultat", "diagnosis": "conclusion"},
    KIND_EXPLORATION: {"resultats": "resultat", "result": "resultat", "conclusion": "diagnosis"},
    KIND_INVOICE: {"number": "invoice_number", "total_amount": "total_amount2", "invoice_date": "date"},
}

# Lignes de détail : valeurs mesurées (laboratoire, exploration), études (imagerie), produits (facture).
MEASURE_FIELDS = (
    "id", "sequence", "code", "name", "rec_name", "result", "result_text", "units",
    "lower_limit", "upper_limit", "normal_range", "remarks", "warning", "test_type_id", "create_date",
)
DETAIL_FIELDS = {
    KIND_LAB: MEASURE_FIELDS + ("gnuhealth_lab_id",),
    KIND_EXPLORATION: MEASURE_FIELDS + ("gnuhealth_exp_id",),
    # Jamais les champs de connexion au serveur d'imagerie (server, server_domain, server_username…).
    KIND_IMAGING: (
        "id", "rec_name", "description", "date", "institution", "ref_physician", "req_physician",
        "imaging_test_number", "imaging_test_order", "instance_uid", "uuid", "link",
    ),
    KIND_INVOICE: ("product_name", "quantity"),
}
DETAIL_ALIASES = {
    "value": "result",
    "unit": "units",
    "unite": "units",
    "reference_range": "normal_range",
    "valeurs_reference": "normal_range",
    "low": "lower_limit",
    "high": "upper_limit",
    "comment": "remarks",
    "abnormal": "warning",
    "product": "product_name",
}

def _empty(value):
    return value is None or (isinstance(value, str) and not value.strip())


def _apply_aliases(data, aliases):
    """Copie `data` en renommant les synonymes, sans écraser un champ déjà fourni."""
    out = {}
    for key, value in data.items():
        target = aliases.get(key, key)
        if target != key and not _empty(data.get(target)):
            continue
        if target in out and not _empty(out[target]):
            continue
        out[target] = value
    return out


def normalize_payload(kind, data):
    """Champs d'un résultat ou d'une facture, tous présents (None si absent), dans l'ordre de référence."""
    if kind not in RESULT_FIELDS or not isinstance(data, dict):
        return data
    source = _apply_aliases(data, ALIASES.get(kind, {}))
    payload = {field: None if _empty(source.get(field)) else source.get(field) for field in RESULT_FIELDS[kind]}
    code_field = CODE_FIELD[kind]
    if kind == KIND_INVOICE:
        payload["invoice_number"] = payload["invoice_number"] or payload["reference"]
        payload["reference"] = payload["reference"] or payload["invoice_number"]
    elif payload.get("rec_name") is None:
        payload["rec_name"] = payload.get(code_field)
    return payload


def normalize_details(kind, details):
    """Lignes de détail réduites aux champs connus, toutes avec les mêmes clés."""
    fields = DETAIL_FIELDS.get(kind)
    if not fields or not isinstance(details, list):
        return details if isinstance(details, list) else []
    normalized = []
    for item in details:
        if not isinstance(item, dict):
            continue
        source = _apply_aliases(item, DETAIL_ALIASES)
        line = {field: source.get(field) for field in fields}
        if kind in (KIND_LAB, KIND_EXPLORATION):
            line["warning"] = bool(line.get("warning"))
            line["result_text"] = line.get("result_text") or ""
            line["remarks"] = line.get("remarks") or ""
            line["rec_name"] = line.get("rec_name") or line.get("name")
        normalized.append(line)
    return normalized
