# Module SaaS de ROHAFYA

ROHAFYA sert désormais plusieurs établissements (*tenants*). Un patient ou un médecin a **un seul compte**, identifié par son **e-mail**, relié à ses dossiers dans chaque établissement par un **lien vérifié** (QR code imprimé sur la facture).

- PDMD Santé devient l'établissement « GNU Health » : ses données restent lues en direct dans Tryton, rien ne change pour ses utilisateurs actuels.
- Les autres établissements envoient leurs données à ROHAFYA par l'**API ROHAFYA** (format de la démo du front-end), en **HL7 FHIR R4**, ou déposent leurs **comptes rendus PDF** (fichier ou scan), relus avant publication.

## Mise en service (interventions manuelles)

1. **Déployer le code** puis redémarrer l'API. Les nouvelles tables (`saas_*`) sont créées automatiquement par `db.create_all()` au démarrage. Aucune table existante n'est modifiée et aucune migration n'est nécessaire.
2. **Initialiser** (idempotent) : crée les rôles `SuperAdmin` et `EstablishmentAdmin`, ainsi que l'établissement GNU Health « PDMD Santé ».
   ```bash
   flask --app Rohafya saas init
   ```
3. **Créer le premier super-administrateur**. Les comptes ayant déjà le rôle historique `Admin` sont aussi super-administrateurs.
   ```bash
   flask --app Rohafya saas create-superadmin admin@exemple.com --first-name Prénom --last-name Nom
   ```
4. **Variable d'environnement `ROHAFYA_FRONT_URL`** (l'ancien nom `EDEN_FRONT_URL` est encore lu) : adresse publique du front-end, utilisée dans les QR codes et les e-mails. Par défaut : `https://rohafya.com`. À définir dans le conteneur, pas dans `env_prod.py`.
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
| `Patient` / `Doctor` | `/patients`, `/doctors` | Inchangés ; les résultats de tous les établissements rattachés sont agrégés |

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

## Suppression d'un établissement

`DELETE /saas/super/tenants/<id>` avec `{"confirm_slug": "<identifiant>"}` (super-administrateur). Efface ses données reçues, imports PDF, rattachements, QR codes et administrateurs ; les comptes ROHAFYA des patients et médecins sont conservés, le journal est gardé (marqué `etablissement_supprime`). Refusé pour l'établissement GNU Health, qui peut seulement être suspendu.

## Ce que voient les utilisateurs

- **Patient** : ses résultats et factures, tous établissements confondus, chacun portant le nom de son établissement. Les règles de chaque établissement s'appliquent : durée d'accès aux détails, blocage si une facture est impayée. L'écran « Mes établissements » permet d'ajouter ou de retirer un établissement.
- **Médecin** : les résultats partagés par ses patients, quel que soit l'établissement. Le module commissions n'apparaît que si un établissement GNU Health l'active.

## Tests

```bash
Rohafya/envDoc/bin/python Rohafya/tests/test_saas.py
```

Ce test de bout en bout tourne sur SQLite en mémoire et couvre : connexion par code, création d'établissement, envoi API et FHIR, QR code, rattachement, cloisonnement entre établissements et droits, imports PDF et quota (si `pdfplumber` est installé), suppression d'un établissement.
