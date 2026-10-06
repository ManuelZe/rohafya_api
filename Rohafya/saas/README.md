# Module SaaS de ROHAFYA

ROHAFYA sert désormais plusieurs établissements (*tenants*). Un patient ou un médecin a **un seul compte**, identifié par son **e-mail**, relié à ses dossiers dans chaque établissement par un **lien vérifié** (QR code imprimé sur la facture).

- PDMD Santé devient l'établissement « GNU Health » : ses données restent lues en direct dans Tryton, rien ne change pour ses utilisateurs actuels.
- Les autres établissements envoient leurs données à ROHAFYA par l'**API ROHAFYA** (format de la démo du front-end), en **HL7 FHIR R4**, ou déposent leurs **comptes rendus PDF** (fichier ou scan), relus avant publication.

## Mise en service (interventions manuelles)

1. **Déployer le code** puis redémarrer l'API. Les nouvelles tables (`saas_*`) sont créées automatiquement par `db.create_all()` au démarrage. Une seule modification de table existante, faite elle aussi au démarrage et seulement si nécessaire (PostgreSQL) : `patient_id` devient facultatif dans `prescriptions` et `save_patients`, pour les envois des médecins. Aucune autre migration n'est nécessaire.
2. **Initialiser** (idempotent, à relancer après chaque mise à jour) :
   - crée les rôles `SuperAdmin` et `EstablishmentAdmin`, l'établissement GNU Health « PDMD Santé » et les permissions du catalogue ;
   - ajoute aux rôles `Patient` et `Doctor` leurs permissions `patients.*` et `doctors.*` manquantes (`--sans-permissions` pour l'éviter) ;
   - rattache à PDMD Santé les prescriptions, pré-enregistrements et requêtes créés avant l'adressage aux établissements.
   ```bash
   flask --app Rohafya saas init
   ```
3. **Créer le premier super-administrateur**. Les comptes ayant déjà le rôle historique `Admin` sont aussi super-administrateurs.
   ```bash
   flask --app Rohafya saas create-superadmin admin@exemple.com --first-name Prénom --last-name Nom
   ```
4. **Adresse du front-end dans les QR codes et les e-mails.** Pour une requête venue d'un front autorisé, l'API reprend l'origine de cette requête (en-tête `Origin`) : `http://localhost:4000` en local, `https://rohafya.com` en ligne. Une origine qui n'est pas dans la liste n'est jamais reprise.
   - **`ROHAFYA_FRONT_ORIGINS`** : fronts autorisés, séparés par des virgules, sans `/` final. Par défaut (`DEFAULT_FRONT_ORIGINS` dans `saas/constants.py`) : `https://rohafya.com`, `https://preprod.rohafya.com`, `http://localhost:4000` et `http://localhost:4200`. La même liste sert à CORS (`__init__.py`, qui y ajoute `http://localhost:8081`).
   - **`ROHAFYA_FRONT_URL`** (l'ancien nom `EDEN_FRONT_URL` est encore lu) : adresse utilisée sans origine reconnue, notamment pour les envois du logiciel de l'établissement (`/ingest`, `/fhir`). Par défaut : `https://rohafya.com`.

   Ces deux variables se définissent dans le conteneur, pas dans `env_prod.py`.
5. **E-mails** : la connexion par code utilise la configuration SMTP existante (`MAIL_*`). Vérifiez qu'un e-mail de test part bien.
6. **Imports PDF — fermés par défaut (« disponible prochainement »)** : tant que la variable d'environnement `ROHAFYA_PDF_IMPORT_ENABLED` (ou l'ancien `EDEN_PDF_IMPORT_ENABLED`) ne vaut pas `true`, toutes les routes d'import PDF (`/saas/admin/tenants/<id>/pdf-imports…` et `POST /ingest/v1/pdf`) répondent **403**, y compris pour le super-administrateur. Pour ouvrir : définir `ROHAFYA_PDF_IMPORT_ENABLED=true` dans le conteneur de l'API, redémarrer, puis passer `PDF_IMPORT_ENABLED` à `true` dans `src/app/saas/features.ts` du front et le reconstruire. Avant cela, installer `pdfplumber` (ajouté à `requirements.txt`) puis redémarrer l'API. Sans lui, les routes d'import PDF répondent 503.
   ```bash
   pip install pdfplumber==0.11.10
   ```
7. **Fuseau horaire du serveur** : le quota d'imports PDF repart à minuit, heure du serveur. Réglez le conteneur sur l'heure locale (ex. `TZ=Africa/Douala`).
8. **Lecture IA des PDF (facultatif)** : `pip install anthropic`, variable `ANTHROPIC_API_KEY`, puis autorisation établissement par établissement dans la console super-admin. À n'activer qu'après accord de l'établissement sur l'envoi de ses PDF à un service externe.

## Rôles

| Rôle | Espace front | Droits |
| --- | --- | --- |
| `SuperAdmin` (ou `Admin` historique) | `/super-admin` | Tous les établissements, leurs administrateurs, leurs clés d'API, tous les comptes, le journal global |
| `EstablishmentAdmin` + appartenance (`saas_tenant_members`) | `/admin` | Uniquement ses établissements : patients, rattachements, QR codes, médecins, données reçues, réglages, journal |
| `Patient` (permissions `patients.*`) | `/patients` | Les résultats de tous les établissements rattachés sont agrégés ; envoie prescriptions, pré-enregistrements et requêtes à l'établissement de son choix |
| `Doctor` (permissions `doctors.*`) | `/doctors` | Résultats partagés ; envoie prescriptions (pour un patient), pré-enregistrements de patients et requêtes à l'établissement de son choix |
| Visiteur sans compte | `/requests` | Requêtes seulement, e-mail obligatoire (la réponse y est envoyée) |

Les permissions `patients.*` et `doctors.*` sont données aux rôles par `flask saas init`. Un administrateur d'établissement n'a besoin d'aucune permission : l'accès à `/saas/admin/tenants/<id>/…` dépend de son appartenance à l'établissement.

## Connexion par code e-mail

- `POST /saas/auth/otp/request` `{email}` : envoie un code à 6 chiffres, valable 10 minutes (5 demandes par heure au maximum).
- `POST /saas/auth/otp/verify` `{email, code, first_name?, last_name?}` : renvoie la même réponse que `user/login`. Si aucun compte n'existe, la réponse est `{"needs_registration": true}`. Il faut alors renvoyer le même code avec le nom et le prénom : un compte patient est créé, avec un identifiant `ROHAFYA-XXXXXXXX` (les identifiants `EDEN-` déjà attribués restent reconnus).
- La connexion historique (identifiant + mot de passe) reste disponible.

## Rattachement par QR code

1. Le logiciel de l'établissement demande un jeton (`POST /ingest/v1/link-tokens`) et imprime sur la facture l'URL (`https://…/l/<jeton>`) sous forme de QR code, ainsi que le code court (ex. `K7P-4QX`).
2. Le patient scanne le QR code, se connecte ou crée son compte avec son e-mail, puis confirme.
3. Si l'e-mail du compte correspond à celui du dossier, le lien est **actif** immédiatement. Sinon, il est **en attente** jusqu'à validation par l'établissement (`/admin/rattachements`).

Règles de sécurité :
- le jeton est aléatoire (128 bits) et seule son empreinte est stockée ;
- il est à usage unique, expire (30 jours par défaut, réglable) et une réimpression annule le précédent ;
- 10 essais ratés par heure et par compte au maximum ;
- un dossier ne peut être rattaché qu'à un seul compte.

## Envoi des données par l'API ROHAFYA

Authentification : en-tête `X-ROHAFYA-API-Key: <clé>` (l'ancien `X-EDEN-API-Key` reste accepté pendant la transition). La clé est générée à la création de l'établissement et renouvelable dans `/admin/integration` ou `/super-admin`.

| Méthode et URL | Corps (liste, 500 éléments maximum) |
| --- | --- |
| `POST /ingest/v1/patients` | `{local_ref, first_name, last_name, email, birth_date, gender, phone}` |
| `POST /ingest/v1/laboratoire` | champs d'un examen de laboratoire de la démo (`name` obligatoire, `test`, `validation_date`…) + `local_ref` + `details: [{name, result, units, normal_range, warning}]` |
| `POST /ingest/v1/imagerie` | idem avec `number` obligatoire (`requested_test`, `conclusion`…) |
| `POST /ingest/v1/exploration` | idem avec `name` obligatoire |
| `POST /ingest/v1/factures` | `{local_ref, reference, date, state, total_amount2, untaxed_amount, amount_to_pay_today, …, products: [{product_name, quantity}]}` |
| `DELETE /ingest/v1/<type>/<code>` | retire un envoi erroné |
| `POST /ingest/v1/link-tokens[?qr=1]` | `{local_ref, email?}` : renvoie `url`, `short_code`, `expires_at` (et `qr_png` en base64 avec `?qr=1`) |
| `GET /ingest/v1/ping` | vérifie la clé |

- Renvoyer un élément avec le même code le met à jour.
- Un lot est enregistré en entier ou pas du tout.
- `local_ref` est le numéro du dossier du patient dans le logiciel de l'établissement.

## Envoi des données en HL7 FHIR R4

`POST /fhir/r4` avec un `Bundle`. Il est aussi possible d'envoyer une ressource seule : `POST /fhir/r4/<Type>`.

| Ressource FHIR | Devient |
| --- | --- |
| `Patient` (`identifier[0].value` = dossier local) | dossier patient |
| `DiagnosticReport` catégorie `LAB` | résultat de laboratoire |
| `DiagnosticReport` catégorie `RAD` / `IMG` | imagerie |
| `DiagnosticReport` autre catégorie | exploration fonctionnelle |
| `Observation` référencée par `DiagnosticReport.result` | valeurs mesurées (`interpretation` H/L… = hors norme) |
| `Invoice` (`issued` / `balanced` / `cancelled`) | facture à régler / payée / annulée |

Les références `urn:uuid:` entre les entrées d'un même Bundle sont résolues.

## Comptes rendus PDF

Méthode « Scan / PDF de résultats » (`source_type = "pdf"`), utilisable aussi par les établissements API ou FHIR. **Rien n'est publié sans la validation d'un administrateur.**

1. Dépôt : page `/admin/imports-pdf` du front, ou `POST /ingest/v1/pdf` (multipart, champ `file`, facultatifs `local_ref`, `exam_code`, `title`) avec la clé d'API.
2. Extraction par règles indépendantes de la mise en page (dictionnaire `analyses_pdf.json`, conversions d'unités, contrôles de vraisemblance). Lecture IA en repli seulement si le super-administrateur l'a autorisée.
3. Relecture : le PDF d'origine à côté des valeurs, modifiables. Les lignes non reconnues et les anomalies doivent être confirmées avant publication.
4. Publication : un résultat de laboratoire (source `pdf`) visible par le patient rattaché.

| Route (préfixe `/saas/admin/tenants/<id>/pdf-imports`) | Rôle |
| --- | --- |
| `GET ""` | liste (`status`, `page`, `page_size`), compteurs, `quota`, `available` |
| `POST ""` | dépôt (multipart) |
| `GET /<n>` · `GET /<n>/fichier` | détail · PDF d'origine |
| `PUT /<n>` | corrections (dossier, date, code, intitulé, valeurs) |
| `POST /<n>/reanalyse` | `{use_ai}` : relance l'extraction |
| `POST /<n>/publier` | `{confirm_warnings}` : 409 + `warnings` si des points restent à confirmer |
| `POST /<n>/rejeter` · `DELETE /<n>` | rejet · suppression (impossible une fois publié) |

**Quota** : `pdf_quota_files` fichiers par période de `pdf_quota_days` jours calendaires (par défaut 10 par jour), réglé par le super-administrateur ; l'administrateur de l'établissement le voit dans ses Paramètres sans pouvoir le modifier. Il compte les dépôts manuels et ceux du logiciel ; supprimer un compte rendu ne rend pas de crédit (décompte sur le journal `pdf.imported`). Au-delà : **429** avec `quota.next_available_at`.

L'import PDF n'est pas proposé à l'établissement GNU Health (ses résultats sont lus directement dans Tryton).

## Prescriptions, pré-enregistrements et requêtes adressés à un établissement

Chaque envoi d'un patient, d'un médecin ou d'un visiteur est adressé à **un établissement actif**. Le contenu reste dans sa table d'origine (`prescriptions`, `save_patients`, `requests`) ; la table `saas_submissions` porte l'adressage : établissement, auteur (`patient`, `doctor`, `anonyme`), patient concerné (envoi d'un médecin), statut (`recue`, `en_cours`, `traitee`, `refusee`), réponse et montant du devis. Code : `saas/submissions.py`.

**Envoi** (champs ajoutés aux routes existantes) :

| Route | Nouveaux champs |
| --- | --- |
| `POST /prescription/add/` (multipart) | `tenant_id` (obligatoire), `audience` (`patient` ou `doctor`), `patient_name` (obligatoire pour un médecin ; nom et numéro d'ordre repris de son profil) |
| `POST /save_patient/add/` (multipart) | `tenant_id`, `audience` ; l'image est obligatoire |
| `POST /requete/add` (JSON, avec ou sans jeton) et `POST /requete/anonym/add` | `tenant_id`, `audience` ; sans compte, `email` obligatoire |
| `GET /saas/public/establishments` | liste publique des établissements actifs, pour les formulaires |

**Lecture** : `GET /prescription/all_prescriptions/?audience=…`, `GET /save_patient/all_save_patients/?audience=…` et `GET /requete/get_requests/<moi>?audience=…` renvoient les seuls envois de l'utilisateur depuis cet espace. Chaque élément porte une clé `submission` (`null` pour un élément antérieur non encore rattaché). Lecture, image et suppression sont réservées à l'auteur, à l'établissement destinataire et aux anciens administrateurs globaux (permissions `administration.*`, qui gardent la vue d'ensemble sans `audience`).

**Console de l'établissement** (administrateurs de l'établissement et super-administrateur) :

- `GET /saas/admin/tenants/<id>/submissions?kind=&status=&q=&page=&page_size=` : liste paginée, avec les compteurs par type et statut ;
- `GET /saas/admin/tenants/<id>/submissions/<sid>/image` : pièce jointe ;
- `PUT /saas/admin/tenants/<id>/submissions/<sid>` `{status, response, quote_amount}` : réponse.

**Notifications** :

- chaque envoi prévient les administrateurs de l'établissement (notification et e-mail `emails/nouvelle_demande.html`). Sans administrateur, l'e-mail part à l'« e-mail de contact » de l'établissement, sinon aux super-administrateurs ;
- chaque réponse prévient l'auteur (notification et e-mail `emails/reponse_demande.html`) ;
- les anciens champs restent cohérents : `requests.valide` / `rejected`, `save_patients.validated`.

## Suppression d'un établissement

`DELETE /saas/super/tenants/<id>` avec `{"confirm_slug": "<identifiant>"}` (super-administrateur). Efface ses données reçues, imports PDF, demandes reçues (adressage et contenu), rattachements, QR codes et administrateurs ; les comptes ROHAFYA des patients et médecins sont conservés, le journal est gardé (marqué `etablissement_supprime`). Refusé pour l'établissement GNU Health, qui peut seulement être suspendu.

## Ce que voient les utilisateurs

- **Patient** : ses résultats et factures, tous établissements confondus, chacun portant le nom de son établissement. Les règles de chaque établissement s'appliquent : durée d'accès aux détails, blocage si une facture est impayée. L'écran « Mes établissements » permet d'ajouter ou de retirer un établissement.
- **Médecin** : les résultats partagés par ses patients, quel que soit l'établissement. Le module commissions n'apparaît que si un établissement GNU Health l'active.
- **Patient et médecin** : chaque prescription, pré-enregistrement ou requête affiche son établissement, son statut et la réponse reçue (montant du devis en FCFA).
- **Administrateur d'établissement** : page « Demandes » (`/admin/demandes`), pour répondre.

## Tests

```bash
Rohafya/envDoc/bin/python Rohafya/tests/test_saas.py
Rohafya/envDoc/bin/python Rohafya/tests/test_demandes.py
```

`test_demandes.py` couvre les envois des patients, des médecins et des visiteurs, le cloisonnement entre établissements, les réponses et notifications, la reprise des envois antérieurs par `flask saas init` et la suppression d'un établissement.

Ce test de bout en bout tourne sur SQLite en mémoire et couvre : connexion par code, création d'établissement, envoi API et FHIR, QR code, rattachement, cloisonnement entre établissements et droits, imports PDF et quota (si `pdfplumber` est installé), suppression d'un établissement.
