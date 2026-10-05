# Connecteur GNU Health → ROHAFYA

Petit programme installé **sur chaque serveur GNU Health**. Toutes les quelques minutes, il lit
GNU Health en local et envoie à ROHAFYA ce qui a changé : patients, résultats de laboratoire,
d'imagerie et d'exploration fonctionnelle, et factures. Il utilise l'API d'ingestion
(`/ingest/v1/*`).

- ROHAFYA n'a plus besoin d'être hébergé sur le serveur GNU Health.
- Les données restent consultables dans ROHAFYA même si le serveur GNU Health est hors ligne.
- Seules des connexions **HTTPS sortantes** partent du serveur GNU Health : aucun port à ouvrir.
- Il n'écrit jamais dans GNU Health : la transaction Tryton est en lecture seule.
- Plusieurs serveurs GNU Health = plusieurs établissements dans ROHAFYA, chacun avec son connecteur et sa clé.

## Ce qui est envoyé

| Donnée | Source GNU Health | Code dans ROHAFYA |
|---|---|---|
| Patients | `party.party` (patients avec matricule) | matricule (`federation_account`) |
| Laboratoire | `gnuhealth.lab` + critères | `name` |
| Imagerie | `gnuhealth.imaging.test.result` + études Orthanc | `number` |
| Exploration | `gnuhealth.exp` + critères (module PDMD Santé) | `name` |
| Factures | `account.invoice` + lignes | numéro de facture |

Les champs sont ceux que l'API renvoyait en lisant Tryton en direct : le front affiche les données
reçues comme avant.

Les règles de l'API sont reprises :
- **Consentement** : seuls les patients dont la case « résultats en ligne » (`result_online`) est cochée sont envoyés, avec leurs résultats et leurs factures.
- **Avoirs** : une facture annulée par un avoir n'est pas envoyée, et l'avoir non plus. Si l'avoir arrive après coup, la facture est retirée de ROHAFYA.

Une base GNU Health sans les modules spécifiques de PDMD Santé fonctionne aussi. Les champs
absents valent `null`, le modèle d'exploration absent est ignoré, et il faut mettre
`filtre_result_online = false` si le champ `result_online` n'existe pas.

## Synchronisation

- **Premier passage :** tout l'historique des patients autorisés est envoyé.
- **Passages suivants :** seulement ce qui a été créé ou modifié depuis le dernier passage réussi, avec 10 minutes de chevauchement. Les envois sont idempotents : un élément déjà reçu est mis à jour, jamais dupliqué.
- **Factures non soldées** (`posted`) : renvoyées à chaque passage, car leur reste à payer du jour change avec les échéances.
- **Nouveau patient, ou patient qui vient de cocher « résultats en ligne » :** tout son historique est envoyé.
- **Après une panne** (ROHAFYA injoignable, erreur) : la date du dernier passage n'avance pas, donc le passage suivant reprend tout ce qui manque. Les erreurs réseau, 429 et 5xx sont retentées 3 fois.

## Installation sur un serveur GNU Health

1. **Dans ROHAFYA**, en super-administrateur : créer l'établissement avec le type **« API »**
   (pas « GNU Health », qui désigne la lecture directe historique), puis copier sa clé d'API.

2. **Repérer comment GNU Health tourne sur ce serveur.** Le connecteur doit être lancé avec le
   **même compte système** et le **même Python** que le serveur trytond. Ce compte peut lire
   `trytond.conf`, qui contient l'accès à la base, et ce Python a les modules GNU Health.
   Le compte s'appelle souvent `gnuhealth`, mais chaque établissement peut avoir choisi un autre nom.
   ```sh
   ps -o user=,args= -p "$(pgrep -f trytond | head -n 1)"
   ```
   La réponse ressemble à :
   ```
   clinique  /home/clinique/.venv/bin/python3 /home/clinique/.venv/bin/trytond -c /home/clinique/gnuhealth/tryton/server/config/trytond.conf
   ```
   On y lit, dans l'ordre : le compte (`clinique`), le Python (`/home/clinique/.venv/bin/python3`)
   et le fichier de configuration (après `-c`). Si `-c` n'apparaît pas, le fichier est donné par la
   variable `TRYTOND_CONFIG` du service :
   ```sh
   sudo cat /proc/"$(pgrep -f trytond | head -n 1)"/environ | tr '\0' '\n' | grep TRYTOND_CONFIG
   ```
   Reporter ces valeurs dans deux variables, utilisées par toutes les commandes suivantes :
   ```sh
   U=clinique                              # compte système de GNU Health
   PY=/home/clinique/.venv/bin/python3     # Python de trytond
   ```

3. **Installer le connecteur** : copier le dossier `connecteur_gnuhealth/` dans `/opt/rohafya/`,
   puis créer la configuration et le journal, réservés à ce compte :
   ```sh
   sudo mkdir -p /etc/rohafya /var/lib/rohafya-connecteur
   sudo cp /opt/rohafya/connecteur_gnuhealth/config.exemple.ini /etc/rohafya/connecteur.ini
   sudo touch /var/log/rohafya-connecteur.log
   sudo chown -R "$U": /etc/rohafya /var/lib/rohafya-connecteur /var/log/rohafya-connecteur.log
   sudo chmod 600 /etc/rohafya/connecteur.ini   # contient la clé d'API
   sudo nano /etc/rohafya/connecteur.ini         # url, cle_api, trytond_conf (chemin lu après -c), base
   ```

4. **Vérifier**, sans rien envoyer :
   ```sh
   cd /opt/rohafya
   sudo -u "$U" "$PY" -m connecteur_gnuhealth -c /etc/rohafya/connecteur.ini verifier
   sudo -u "$U" "$PY" -m connecteur_gnuhealth -c /etc/rohafya/connecteur.ini synchro --essai
   ```
   Si le compte ou le Python ne sont pas les bons, `verifier` le signale : `trytond.conf`
   illisible, ou module `trytond` introuvable. `--essai` affiche le nombre d'éléments à envoyer
   et un exemple de chaque type ; c'est le bon moment pour contrôler les données avant le premier
   envoi.

5. **Premier envoi**, puis automatisation toutes les 5 minutes dans la crontab de ce compte :
   ```sh
   sudo -u "$U" "$PY" -m connecteur_gnuhealth -c /etc/rohafya/connecteur.ini synchro
   ( sudo crontab -u "$U" -l 2>/dev/null
     echo "*/5 * * * * cd /opt/rohafya && $PY -m connecteur_gnuhealth -c /etc/rohafya/connecteur.ini synchro >/dev/null 2>&1"
   ) | sudo crontab -u "$U" -
   ```
   Le journal est écrit dans `/var/log/rohafya-connecteur.log` (paramètre `journal`). Si un
   passage dure plus de 5 minutes, le suivant s'arrête tout de suite au lieu de tourner en
   parallèle.

   Le connecteur peut aussi tourner sous un compte dédié, par exemple `rohafya`. Ce compte doit
   pouvoir lire `trytond.conf`, par exemple via un groupe commun. Il faut alors remplacer `$U`
   par ce compte dans les commandes ci-dessus.

## Commandes

| Commande | Effet |
|---|---|
| `verifier` | Teste la clé ROHAFYA et l'ouverture de la base GNU Health. |
| `synchro` | Envoie ce qui a changé depuis le dernier passage réussi. |
| `synchro --complet` | Renvoie tout l'historique (sans risque : rien n'est dupliqué). |
| `synchro --essai` | Lit et convertit sans rien envoyer ni enregistrer. |
| `lien <matricule> [--email …] [--qr fichier.png]` | Crée le QR code et le code court de rattachement d'un patient, à imprimer sur sa facture. |

## Limites connues

- Une donnée **supprimée** dans GNU Health n'est pas retirée de ROHAFYA (sauf les factures annulées par un avoir).
- Un patient qui **décoche** « résultats en ligne » n'est plus mis à jour, mais ses données déjà envoyées restent dans ROHAFYA.
- **Factures impayées** : GNU Health ne bloque les résultats que pour les factures créées depuis le 1ᵉʳ décembre 2025, alors que ROHAFYA prend en compte toutes les factures reçues. Un patient avec une vieille facture impayée peut donc voir ses détails bloqués. La date de création est envoyée (`create_date`) pour que l'API puisse appliquer la même règle.
- **Commissions, listes de prix (devis) et médecins** ne sont pas encore envoyés : ces écrans lisent toujours GNU Health en direct.

## Tests

Depuis la racine du dépôt :
```sh
Rohafya/envDoc/bin/python connecteur_gnuhealth/tests/test_connecteur.py
```
Le test fait tourner le connecteur sur une fausse base GNU Health et l'API d'ingestion réelle
(SQLite en mémoire), puis vérifie ce que voit un patient rattaché.
