"""Conversion des enregistrements GNU Health au format d'ingestion ROHAFYA.

Les champs sont ceux que l'API renvoyait jusqu'ici en lisant Tryton en direct
(patients/laboratoire, patients/imagerie, patients/exploration, patients/charge_patients),
pour que le front affiche les données reçues exactement comme avant.

Les champs calculés par ROHAFYA à l'affichage (id, establishment, expiration_date,
nbr_days_before_expiration, statut_expiration, error) ne sont pas envoyés.

Chaque accès passe par `champ()` : un champ absent (GNU Health sans les modules
spécifiques de PDMD Santé) donne None au lieu d'une erreur.
"""
import datetime
from decimal import Decimal


def champ(obj, chemin, defaut=None):
    """Valeur de `obj.a.b.c`, ou `defaut` si un maillon est vide ou n'existe pas."""
    for nom in chemin.split("."):
        if obj is None:
            return defaut
        try:
            obj = getattr(obj, nom)
        except (AttributeError, KeyError):
            return defaut
    return defaut if obj is None else obj


def nom_complet(personne):
    """Nom d'un professionnel de santé ou d'un patient GNU Health (party.name + party.lastname)."""
    if personne is None:
        return None
    morceaux = [champ(personne, "name.name"), champ(personne, "name.lastname")]
    texte = " ".join(str(m) for m in morceaux if m)
    return texte or None


def valeur_json(valeur):
    """Valeur sérialisable en JSON (dates ISO 8601, décimaux en nombres, enregistrements par leur nom)."""
    if valeur is None or isinstance(valeur, (bool, int, float, str)):
        return valeur
    if isinstance(valeur, Decimal):
        return float(valeur)
    if isinstance(valeur, (datetime.datetime, datetime.date, datetime.time)):
        return valeur.isoformat()
    if isinstance(valeur, (bytes, bytearray, memoryview)):
        return None
    rec_name = getattr(valeur, "rec_name", None)
    if isinstance(rec_name, str):
        return rec_name
    return str(valeur)


def _json(donnees):
    return {cle: valeur_json(valeur) for cle, valeur in donnees.items()}


def _nombre(valeur):
    try:
        return float(valeur)
    except (TypeError, ValueError):
        return 0.0


# =====================================================================
# Patients
# =====================================================================

def patient(party):
    """Patient (party.party) ; local_ref = matricule GNU Health (federation_account)."""
    return _json({
        "local_ref": champ(party, "federation_account"),
        "first_name": champ(party, "name"),
        "last_name": champ(party, "lastname"),
        "email": champ(party, "email"),
        "phone": champ(party, "phone"),
        "birth_date": champ(party, "dob"),
        "gender": champ(party, "gender"),
    })


# =====================================================================
# Résultats d'examens
# =====================================================================

def _criteres(examen, champ_ordre):
    """Valeurs mesurées (critearea) d'un examen de laboratoire ou d'exploration."""
    details = []
    for critere in champ(examen, "critearea", []) or []:
        details.append(_json({
            "code": champ(critere, "code"),
            "create_date": champ(critere, "create_date"),
            champ_ordre: champ(critere, champ_ordre + ".request_order"),
            "id": champ(critere, "id"),
            "lower_limit": champ(critere, "lower_limit"),
            "name": champ(critere, "name"),
            "normal_range": champ(critere, "normal_range"),
            "rec_name": champ(critere, "rec_name"),
            "remarks": champ(critere, "remarks"),
            "result": champ(critere, "result"),
            "result_text": champ(critere, "result_text"),
            "sequence": champ(critere, "sequence"),
            "test_type_id": champ(critere, "test_type_id.name"),
            "units": champ(critere, "units.name"),
            "upper_limit": champ(critere, "upper_limit"),
            "warning": champ(critere, "warning"),
        }))
    return details


def laboratoire(examen, matricule, prescripteur=None):
    """Résultat de laboratoire (gnuhealth.lab) ; code = name."""
    donnees = _json({
        "local_ref": matricule,
        "analytes_summary": champ(examen, "analytes_summary"),
        "date_analysis": champ(examen, "date_analysis"),
        "date_requested": champ(examen, "date_requested"),
        "diagnosis": champ(examen, "diagnosis"),
        "done_by": nom_complet(champ(examen, "done_by")),
        "done_date": champ(examen, "done_date"),
        "historize": champ(examen, "historize"),
        "macroscopie": champ(examen, "macroscopie"),
        "microscopie": champ(examen, "microscopie"),
        "name": champ(examen, "name"),
        "patient": nom_complet(champ(examen, "patient")),
        "matricule_patient": matricule,
        "rec_name": champ(examen, "rec_name"),
        "renseignements": champ(examen, "renseignements"),
        "request_order": champ(examen, "request_order"),
        "requestor": prescripteur,
        "results": champ(examen, "results"),
        "serializer": champ(examen, "serializer"),
        "serializer_current": champ(examen, "serializer_current"),
        "state": champ(examen, "state"),
        "test": champ(examen, "test.name"),
        "validated_by": nom_complet(champ(examen, "validated_by")),
        "validated_fed_id": champ(examen, "validated_by.name.federation_account"),
        "validation_date": champ(examen, "validation_date"),
    })
    donnees["details"] = _criteres(examen, "gnuhealth_lab_id")
    return donnees


def exploration(examen, matricule, prescripteur=None):
    """Exploration fonctionnelle (gnuhealth.exp, module PDMD Santé) ; code = name."""
    donnees = _json({
        "local_ref": matricule,
        "analytes_summary": champ(examen, "analytes_summary"),
        "commentaire": champ(examen, "commentaire"),
        "date_analysis": champ(examen, "date_analysis"),
        "date_requested": champ(examen, "date_requested"),
        "diagnosis": champ(examen, "diagnosis"),
        "done_by": nom_complet(champ(examen, "done_by")),
        "done_date": champ(examen, "done_date"),
        "indication": champ(examen, "indication"),
        "historize": champ(examen, "historize"),
        "name": champ(examen, "name"),
        "pathologist": nom_complet(champ(examen, "pathologist")),
        "patient": nom_complet(champ(examen, "patient")),
        "matricule_patient": matricule,
        "rec_name": champ(examen, "rec_name"),
        "request_order": champ(examen, "request_order"),
        "requestor": prescripteur,
        "realisateur": nom_complet(champ(examen, "realisateur")),
        "results": champ(examen, "results"),
        "resultat": champ(examen, "resultat"),
        "serializer": champ(examen, "serializer"),
        "state": champ(examen, "state"),
        "test": champ(examen, "test.name"),
        "technique": champ(examen, "technique"),
        "traitement": champ(examen, "traitement"),
        "validated_by": nom_complet(champ(examen, "validated_by")),
        "validated_fed_id": champ(examen, "validated_by.name.federation_account"),
        "validation_date": champ(examen, "validation_date"),
    })
    donnees["details"] = _criteres(examen, "gnuhealth_exp_id")
    return donnees


def imagerie(examen, matricule, prescripteur=None):
    """Résultat d'imagerie (gnuhealth.imaging.test.result) ; code = number ; détails = études Orthanc."""
    donnees = _json({
        "local_ref": matricule,
        "date": champ(examen, "date"),
        "request_date": champ(examen, "request_date"),
        "create_date": champ(examen, "create_date"),
        "computed_age": champ(examen, "computed_age"),
        "create_uid": champ(examen, "create_uid.name"),
        "done_by": nom_complet(champ(examen, "done_by")),
        "done_date": champ(examen, "done_date"),
        "doctor": nom_complet(champ(examen, "doctor")),
        "conclusion": champ(examen, "conclusion"),
        "indication": champ(examen, "indication"),
        "merge_id": champ(examen, "merge_id"),
        "number": champ(examen, "number"),
        "order": champ(examen, "order"),
        "rec_name": champ(examen, "rec_name"),
        "patient": nom_complet(champ(examen, "patient")),
        "matricule_patient": matricule,
        "realisateur": nom_complet(champ(examen, "realisateur")),
        "request_order": champ(examen, "request.request"),
        "service_cot": champ(examen, "request.service.name"),
        "requested_test": champ(examen, "requested_test.name"),
        "requestor": prescripteur,
        "resultat": champ(examen, "resultat"),
        "serializer": champ(examen, "serializer"),
        "serializer_current": champ(examen, "serializer_current"),
        "state": champ(examen, "state"),
        "technique": champ(examen, "technique"),
        "validated_by": nom_complet(champ(examen, "validated_by")),
        "validated_fed_id": champ(examen, "validated_by.name.federation_account"),
        "validation_date": champ(examen, "validation_date"),
    })
    details = []
    for etude in champ(examen, "studies", []) or []:
        details.append(_json({
            "create_date": champ(etude, "create_date"),
            "create_uid": champ(etude, "create_uid.name"),
            "date": champ(etude, "date"),
            "description": champ(etude, "description"),
            "id": champ(etude, "id"),
            "ident": champ(etude, "ident"),
            "imaging_test_number": champ(etude, "imaging_test.number"),
            "imaging_test_order": champ(etude, "imaging_test.order"),
            "instance_uid": champ(etude, "instance_uid"),
            "institution": champ(etude, "institution"),
            "link": champ(etude, "link"),
            "merge_id": champ(etude, "merge_id"),
            "patient_orthanc": champ(etude, "patient.name"),
            "patient_orthanc_recname": champ(etude, "patient.rec_name"),
            "patient_orthanc_gnuhealth": nom_complet(champ(etude, "patient.patient")),
            "rec_name": champ(etude, "rec_name"),
            "ref_physician": champ(etude, "ref_phys"),
            "req_physician": champ(etude, "req_phys"),
            "requested_procedure_id": champ(etude, "requested_procedure_id"),
            "server": champ(etude, "server.rec_name"),
            "server_domain": champ(etude, "server.domain"),
            "server_username": champ(etude, "server.user"),
            "uuid": champ(etude, "uuid"),
            "write_date": champ(etude, "write_date"),
            "write_uid": champ(etude, "write_uid.name"),
        }))
    donnees["details"] = details
    return donnees


# =====================================================================
# Factures
# =====================================================================

def facture(invoice, matricule, lignes):
    """Facture (account.invoice) ; code = numéro de facture.

    `reference` porte le numéro de facture : c'est le code sous lequel ROHAFYA range la facture
    et que le front utilise pour en demander les lignes. La référence d'origine de GNU Health
    (numéro de la facture annulée, pour un avoir) n'a pas de sens une fois les avoirs écartés.
    """
    montant_patient = champ(invoice, "montant_patient")
    donnees = _json({
        "local_ref": matricule,
        "invoice_number": champ(invoice, "number"),
        "reference": champ(invoice, "number"),
        "date": champ(invoice, "invoice_date"),
        "amount_to_pay": champ(invoice, "amount_to_pay"),
        "montant_assurance": champ(invoice, "montant_assurance"),
        # Même règle que l'API : un reste patient de 25 ou moins est affiché à 0.
        "montant_patient": montant_patient if _nombre(montant_patient) > 25 else 0.0,
        "untaxed_amount": _nombre(champ(invoice, "untaxed_amount")),
        "amount_to_pay_today": _nombre(champ(invoice, "amount_to_pay_today")),
        "state": champ(invoice, "state"),
        "total_amount2": _nombre(champ(invoice, "total_amount2", champ(invoice, "total_amount"))),
        "create_date": champ(invoice, "create_date"),
    })
    donnees["products"] = [
        _json({"product_name": champ(ligne, "product.name"), "quantity": champ(ligne, "quantity")})
        for ligne in lignes
    ]
    return donnees


def factures_annulees(factures):
    """Écarte les factures annulées par un avoir, et les avoirs eux-mêmes (même règle que l'API).

    `factures` : [(numéro, référence)] de toutes les factures d'un patient.
    Une facture dont le numéro est la référence d'une autre est annulée ; l'avoir qui la
    référence est écarté aussi. Renvoie (numéros écartés, {avoir: facture annulée}).
    """
    numeros = {numero for numero, _ in factures if numero}
    references = {reference for _, reference in factures if reference}
    annules = set()
    avoirs = {}
    for numero, reference in factures:
        if not numero:
            continue
        if numero in references:
            annules.add(numero)
        if reference and reference in numeros:
            annules.add(numero)
            avoirs[numero] = reference
    return annules, avoirs
