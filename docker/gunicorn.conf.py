"""Configuration de gunicorn dans le conteneur (surchargeable par variables d'environnement)."""
import multiprocessing
import os

bind = "0.0.0.0:" + os.environ.get("PORT", "8000")

# Par défaut : 2 processus par cœur + 1, plafonné à 8 (chaque processus garde sa connexion à la base).
workers = int(os.environ.get("GUNICORN_WORKERS", min(multiprocessing.cpu_count() * 2 + 1, 8)))
threads = int(os.environ.get("GUNICORN_THREADS", "2"))
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "120"))
graceful_timeout = 30
keepalive = 5

# Un processus est recyclé après N requêtes (limite les fuites de mémoire).
max_requests = int(os.environ.get("GUNICORN_MAX_REQUESTS", "2000"))
max_requests_jitter = 200

# Journaux sur la sortie standard : visibles dans Portainer (Containers → Logs).
accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOGLEVEL", "info")
access_log_format = '%({x-forwarded-for}i)s %(h)s "%(r)s" %(s)s %(b)s %(M)sms "%(a)s"'

# Fichiers temporaires des processus en mémoire (évite les blocages sur le système de fichiers Docker).
worker_tmp_dir = "/dev/shm"
