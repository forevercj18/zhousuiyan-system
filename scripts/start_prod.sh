#!/usr/bin/env sh
set -eu

export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings_prod}"

echo "[STEP] Django deploy check"
python manage.py check --deploy

echo "[STEP] Apply migrations"
python manage.py migrate --noinput

echo "[STEP] Collect static files"
python manage.py collectstatic --noinput

echo "[STEP] Start gunicorn"
exec gunicorn config.wsgi:application \
  --bind "${GUNICORN_BIND:-0.0.0.0:8000}" \
  --workers "${GUNICORN_WORKERS:-2}" \
  --timeout "${GUNICORN_TIMEOUT:-120}" \
  --access-logfile - \
  --error-logfile -
