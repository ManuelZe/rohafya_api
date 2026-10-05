#!/bin/sh
# Démarrage du conteneur de l'API ROHAFYA :
# 1. attend que PostgreSQL accepte les connexions ;
# 2. crée les tables manquantes une seule fois (avant les processus gunicorn, qui le feraient
#    sinon en parallèle au premier démarrage) ;
# 3. lance la commande du conteneur (gunicorn par défaut, ou une commande d'administration).
set -e

python - <<'EOF'
import os, sys, time
from sqlalchemy import create_engine, text

url = os.environ.get("ROHAFYA_DATABASE_URL")
if not url:
    sys.exit("ROHAFYA_DATABASE_URL manquante.")
engine = create_engine(url, pool_pre_ping=True)
for essai in range(1, 31):
    try:
        with engine.connect() as connexion:
            connexion.execute(text("SELECT 1"))
        print("Base de données disponible.", flush=True)
        break
    except Exception as erreur:
        print(f"Base de données indisponible (essai {essai}/30) : {erreur.__class__.__name__}", flush=True)
        time.sleep(2)
else:
    sys.exit("Base de données injoignable après 60 secondes.")
EOF

if [ "${ROHAFYA_SKIP_INIT:-false}" != "true" ]; then
    python -c "from Rohafya import create_app; create_app()" \
        && echo "Tables vérifiées."
fi

exec "$@"
