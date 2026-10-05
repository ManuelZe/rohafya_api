# DoctorAPI

## Installation

1. **Installation des dépendances**
   Après avoir créé et activé l'environnement virtuel :
   ```bash
   pip3 install -r requirements.txt
   ```

2. **Base de données**
   Créer la base de données nommée `doctor`.

3. **Configuration**
   Changer le port POSTGRES dans le chemin URI du fichier `__init__.py`.

4. **Lancement**
   Lancer l'application avec Gunicorn (adapter l'IP et le port si nécessaire) :
   ```bash
   gunicorn -w 4 -b 172.17.0.7:7600 "DoctorAPI:create_app()"
   ```

## Migrations (Flask-Migrate)

* **Installation** : `pip install Flask-Migrate`
* **Initialisation** : `migrate = Migrate(app, db)`
* **Init DB** : `flask --app DoctorAPI db init`
* **Créer une migration** : `flask --app DoctorAPI db migrate -m "Message"`
* **Mise à jour DB** : `flask --app DoctorAPI db upgrade`

## Permissions

Pour ajouter les permissions :
```bash
cd /home/gnuhealth/DoctorAPI
python3 -m DoctorAPI.permissions.__init__permission
```

## Procédures

### Docteurs

1. **Chargement** : Import des docteurs depuis Stone pdmd Santé (récupération du `FEDERATIONID`).
2. **Communication** : Envoi du `FEDERATIONID` au docteur par Whatsapp après récupération de son email.
3. **Connexion** : Le docteur entre son `FEDERATIONID`. Il reçoit ses identifiants (username/password) par email.
4. **Utilisation** : Le docteur complète ses informations pour accéder à ses commissions journalières et à la liste des patients.

### Patients

1. **Enrôlement** : Lors de la visite, demander si le patient souhaite ses résultats en ligne.
2. **Configuration** :
   - Dans GNU Health, cocher la case indiquant que le patient désire ses informations en ligne (modèle `gnuhealth.patients`).
   - Remettre le `FEDERATIONID` au patient et récupérer son email.
3. **Connexion** : Le patient se connecte avec son `FEDERATIONID` et reçoit ses identifiants par email.
# rohafya_api
# rohafya_api
