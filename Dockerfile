# Image de l'API ROHAFYA (Flask + gunicorn), sans GNU Health.
#   docker build -t rohafya-api .
# Documentation : docs/DEPLOIEMENT_DOCKER.md

FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    # Déploiement conteneur : pas de lecture directe de GNU Health (données reçues par le connecteur).
    ROHAFYA_GNUHEALTH=false \
    ROHAFYA_UPLOAD_FOLDER=/app/files \
    FLASK_APP=Rohafya

WORKDIR /app

COPY docker/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt && rm /tmp/requirements.txt

# Utilisateur sans privilèges ; le dossier des fichiers envoyés lui appartient (volume).
RUN groupadd --system --gid 10001 rohafya \
    && useradd --system --uid 10001 --gid rohafya --home-dir /app --shell /usr/sbin/nologin rohafya \
    && mkdir -p /app/files \
    && chown rohafya:rohafya /app/files

COPY --chmod=755 docker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY docker/gunicorn.conf.py /app/gunicorn.conf.py
COPY Rohafya /app/Rohafya

# Version de l'image (renseignée par GitHub Actions), affichée dans les journaux et par docker inspect.
ARG VERSION=dev
ENV ROHAFYA_VERSION=${VERSION}
LABEL org.opencontainers.image.title="rohafya-api" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.source="https://github.com/ManuelZe/rohafya_api"

USER rohafya
EXPOSE 8000
VOLUME ["/app/files"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/sante', timeout=4).status == 200 else 1)"

ENTRYPOINT ["entrypoint.sh"]
CMD ["gunicorn", "--config", "/app/gunicorn.conf.py", "Rohafya:create_app()"]
