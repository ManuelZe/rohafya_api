"""Catalogue initial du bilan prénuptial (Documentation/ROHAFYA_Fiche_Examens_Prenuptiaux.md du front).

Inséré une seule fois, quand la table du catalogue est vide ; le super-administrateur le modifie ensuite
depuis sa console. Les valeurs sont des intervalles habituels chez l'adulte : l'intervalle imprimé par le
laboratoire fait foi.
"""

SEX_ALL = "all"
SEX_FEMALE = "F"
SEX_MALE = "M"
SEXES = (SEX_ALL, SEX_FEMALE, SEX_MALE)


def _exam(code, sex, label_fr, label_en, purpose_fr, purpose_en, reference_fr, reference_en, unit=""):
    return {
        "code": code,
        "sex": sex,
        "label_fr": label_fr,
        "label_en": label_en,
        "purpose_fr": purpose_fr,
        "purpose_en": purpose_en,
        "reference_fr": reference_fr,
        "reference_en": reference_en,
        "unit": unit,
    }


DEFAULT_EXAMS = [
    # --- Pour les deux partenaires -----------------------------------------
    _exam("ABO_RH", SEX_ALL, "Groupe sanguin ABO et rhésus (Rh D)", "ABO blood group and Rhesus (Rh D)",
          "Prévenir l'incompatibilité rhésus lors d'une grossesse ; transfusion",
          "Prevent Rhesus incompatibility in pregnancy; transfusion",
          "A, B, AB ou O, et Rh positif ou négatif", "A, B, AB or O, and Rh positive or negative"),
    _exam("HB_ELECTROPHORESIS", SEX_ALL, "Électrophorèse de l'hémoglobine", "Haemoglobin electrophoresis",
          "Dépister la drépanocytose et les autres anomalies de l'hémoglobine",
          "Screen for sickle cell disease and other haemoglobin disorders",
          "Profil AA : HbA 95 à 98 % ; HbA2 2 à 3,5 % ; HbF < 1 % ; pas d'HbS ni d'HbC",
          "AA profile: HbA 95–98%; HbA2 2–3.5%; HbF < 1%; no HbS or HbC"),
    _exam("CBC_WBC", SEX_ALL, "NFS : globules blancs", "CBC: white blood cells",
          "Infection, anomalie de la moelle", "Infection, bone marrow disorder",
          "4,0 à 10,0 G/L (3,0 à 4,0 G/L peut être normal chez une partie des adultes d'origine africaine)",
          "4.0–10.0 G/L (3.0–4.0 G/L can be normal in some adults of African descent)", "G/L"),
    _exam("CBC_PLATELETS", SEX_ALL, "NFS : plaquettes", "CBC: platelets",
          "Troubles de la coagulation", "Clotting disorders", "150 à 400 G/L", "150–400 G/L", "G/L"),
    _exam("CBC_MCV", SEX_ALL, "NFS : volume globulaire moyen (VGM)", "CBC: mean corpuscular volume (MCV)",
          "Orientation d'une anémie (carence en fer, thalassémie)", "Anaemia work-up (iron deficiency, thalassaemia)",
          "80 à 100 fL", "80–100 fL", "fL"),
    _exam("FASTING_GLUCOSE", SEX_ALL, "Glycémie à jeun", "Fasting blood glucose", "Diabète", "Diabetes",
          "0,70 à 1,00 g/L (3,9 à 5,5 mmol/L) ; 1,26 g/L ou plus à deux reprises : diabète",
          "0.70–1.00 g/L (3.9–5.5 mmol/L); 1.26 g/L or more twice: diabetes", "g/L"),
    _exam("HIV", SEX_ALL, "Sérologie VIH 1 et 2", "HIV 1 and 2 serology", "Infection par le VIH", "HIV infection",
          "Négative", "Negative"),
    _exam("HBSAG", SEX_ALL, "Antigène HBs (hépatite B)", "HBs antigen (hepatitis B)",
          "Infection par le virus de l'hépatite B", "Hepatitis B infection", "Négatif", "Negative"),
    _exam("ANTI_HBS", SEX_ALL, "Anticorps anti-HBs", "Anti-HBs antibodies", "Protection contre l'hépatite B",
          "Protection against hepatitis B",
          "10 UI/L ou plus : protégé ; moins de 10 UI/L : non protégé, vaccination à discuter",
          "10 IU/L or more: protected; under 10 IU/L: not protected, discuss vaccination", "UI/L"),
    _exam("ANTI_HCV", SEX_ALL, "Anticorps anti-VHC (hépatite C)", "Anti-HCV antibodies (hepatitis C)",
          "Infection par le virus de l'hépatite C", "Hepatitis C infection", "Négatifs", "Negative"),
    _exam("SYPHILIS", SEX_ALL, "Syphilis (TPHA et VDRL)", "Syphilis (TPHA and VDRL)", "Syphilis", "Syphilis",
          "Négatifs", "Negative"),
    _exam("BLOOD_PRESSURE", SEX_ALL, "Pression artérielle", "Blood pressure", "Hypertension", "Hypertension",
          "Moins de 140/90 mmHg ; idéalement moins de 120/80 mmHg", "Under 140/90 mmHg; ideally under 120/80 mmHg",
          "mmHg"),
    _exam("BMI", SEX_ALL, "Indice de masse corporelle", "Body mass index", "Poids", "Weight", "18,5 à 24,9 kg/m²",
          "18.5–24.9 kg/m²", "kg/m²"),
    # --- Femme ---------------------------------------------------------------
    _exam("F_HEMOGLOBIN", SEX_FEMALE, "Hémoglobine", "Haemoglobin", "Anémie", "Anaemia", "12,0 à 16,0 g/dL",
          "12.0–16.0 g/dL", "g/dL"),
    _exam("F_HEMATOCRIT", SEX_FEMALE, "Hématocrite", "Haematocrit", "Anémie", "Anaemia", "36 à 47 %", "36–47%", "%"),
    _exam("F_RBC", SEX_FEMALE, "Globules rouges", "Red blood cells", "Anémie", "Anaemia", "4,0 à 5,2 T/L",
          "4.0–5.2 T/L", "T/L"),
    _exam("F_FERRITIN", SEX_FEMALE, "Ferritine", "Ferritin", "Réserves en fer, avant une grossesse",
          "Iron stores, before a pregnancy",
          "15 à 150 µg/L (carence souvent retenue sous 30 µg/L : suivre le seuil du laboratoire)",
          "15–150 µg/L (deficiency often defined below 30 µg/L: follow the lab's threshold)", "µg/L"),
    _exam("F_CREATININE", SEX_FEMALE, "Créatinine", "Creatinine", "Fonction des reins", "Kidney function",
          "5 à 11 mg/L (45 à 97 µmol/L)", "5–11 mg/L (45–97 µmol/L)", "mg/L"),
    _exam("RUBELLA_IGG", SEX_FEMALE, "Sérologie de la rubéole (IgG)", "Rubella serology (IgG)",
          "Protection contre la rubéole, dangereuse pendant la grossesse",
          "Protection against rubella, dangerous during pregnancy",
          "10 UI/mL ou plus : immunisée ; moins de 10 UI/mL : vaccination avant la grossesse",
          "10 IU/mL or more: immune; under 10 IU/mL: vaccinate before pregnancy", "UI/mL"),
    _exam("TOXOPLASMOSIS", SEX_FEMALE, "Sérologie de la toxoplasmose (IgG et IgM)", "Toxoplasmosis serology (IgG and IgM)",
          "Protection contre la toxoplasmose, dangereuse pendant la grossesse",
          "Protection against toxoplasmosis, dangerous during pregnancy",
          "IgG positives et IgM négatives : immunisée ; IgG négatives : non immunisée",
          "IgG positive and IgM negative: immune; IgG negative: not immune"),
    _exam("PAP_HPV", SEX_FEMALE, "Frottis cervico-utérin ou test HPV (dès 25 ans)", "Pap smear or HPV test (from age 25)",
          "Dépistage du cancer du col de l'utérus", "Cervical cancer screening", "Normal (négatif)", "Normal (negative)"),
    _exam("CHLAMYDIA", SEX_FEMALE, "Recherche de Chlamydia trachomatis (facultatif)", "Chlamydia trachomatis test (optional)",
          "Infection sexuellement transmissible, cause d'infertilité", "Sexually transmitted infection, cause of infertility",
          "Négative", "Negative"),
    # --- Homme ---------------------------------------------------------------
    _exam("M_HEMOGLOBIN", SEX_MALE, "Hémoglobine", "Haemoglobin", "Anémie", "Anaemia", "13,0 à 17,0 g/dL",
          "13.0–17.0 g/dL", "g/dL"),
    _exam("M_HEMATOCRIT", SEX_MALE, "Hématocrite", "Haematocrit", "Anémie", "Anaemia", "40 à 54 %", "40–54%", "%"),
    _exam("M_RBC", SEX_MALE, "Globules rouges", "Red blood cells", "Anémie", "Anaemia", "4,5 à 5,9 T/L",
          "4.5–5.9 T/L", "T/L"),
    _exam("M_FERRITIN", SEX_MALE, "Ferritine", "Ferritin", "Réserves en fer", "Iron stores", "30 à 300 µg/L",
          "30–300 µg/L", "µg/L"),
    _exam("M_CREATININE", SEX_MALE, "Créatinine", "Creatinine", "Fonction des reins", "Kidney function",
          "7 à 13 mg/L (62 à 115 µmol/L)", "7–13 mg/L (62–115 µmol/L)", "mg/L"),
    _exam("SEMEN_VOLUME", SEX_MALE, "Spermogramme : volume (facultatif)", "Semen analysis: volume (optional)",
          "Fertilité", "Fertility", "1,4 mL ou plus (OMS 2021)", "1.4 mL or more (WHO 2021)", "mL"),
    _exam("SEMEN_CONCENTRATION", SEX_MALE, "Spermogramme : concentration", "Semen analysis: concentration",
          "Fertilité", "Fertility", "16 millions/mL ou plus", "16 million/mL or more", "millions/mL"),
    _exam("SEMEN_TOTAL", SEX_MALE, "Spermogramme : nombre total", "Semen analysis: total count", "Fertilité",
          "Fertility", "39 millions ou plus par éjaculat", "39 million or more per ejaculate", "millions"),
    _exam("SEMEN_PROGRESSIVE", SEX_MALE, "Spermogramme : mobilité progressive", "Semen analysis: progressive motility",
          "Fertilité", "Fertility", "30 % ou plus", "30% or more", "%"),
    _exam("SEMEN_MOTILITY", SEX_MALE, "Spermogramme : mobilité totale", "Semen analysis: total motility",
          "Fertilité", "Fertility", "42 % ou plus", "42% or more", "%"),
    _exam("SEMEN_MORPHOLOGY", SEX_MALE, "Spermogramme : formes normales", "Semen analysis: normal forms",
          "Fertilité", "Fertility", "4 % ou plus", "4% or more", "%"),
    _exam("SEMEN_VITALITY", SEX_MALE, "Spermogramme : vitalité", "Semen analysis: vitality", "Fertilité",
          "Fertility", "54 % ou plus", "54% or more", "%"),
]
