# gunicorn_config.py

# Nom du module et de l'instance de l'application Flask
wsgi_app = "_init__:app"  # Remplacez 'app' par le nom de votre fichier et instance si différent

# Adresse IP et port d'écoute
bind = "172.22.0.2:4045"  # Écouter sur tous les IPs et sur le port 8000

# Nombre de workers (processus gérant les requêtes)
workers = 4  # Ajustez en fonction des ressources de votre serveur

# Nombre de threads par worker
threads = 2  # Optionnel, mais utile si votre application utilise des threads

# Mode daemon pour exécuter Gunicorn en arrière-plan
daemon = True

# Chemin des fichiers de log
accesslog = "/var/log/gunicorn/access.log"  # Fichier de log pour les requêtes
errorlog = "/var/log/gunicorn/error.log"    # Fichier de log pour les erreurs
loglevel = "info"  # Niveau de log : debug, info, warning, error, critical

# Paramètres supplémentaires
timeout = 120  # Temps maximum d'attente pour une requête (en secondes)
