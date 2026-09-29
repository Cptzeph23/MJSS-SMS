#!/usr/bin/env bash
# Absolute path: SMS/deploy/build.sh
#
# Render build command. Set this as the "Build Command" in the Render
# dashboard (or reference it from render.yaml, see deploy/render.yaml).
#
# Installs system-level dependencies that `pip install` cannot provide:
#   - WeasyPrint (Phase 9, report/transcript/receipt PDF generation)
#     needs Cairo, Pango, and GDK-PixBuf.
#   - python-magic (Phase 24, file-upload content validation) needs
#     libmagic.
set -o errexit  # exit immediately if any command fails

echo "==> Installing system dependencies (WeasyPrint + libmagic)..."
apt-get update -qq
apt-get install -y -qq \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libgdk-pixbuf2.0-0 \
    libcairo2 \
    libffi-dev \
    libmagic1

echo "==> Installing Python dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "==> Collecting static files..."
python manage.py collectstatic --noinput

echo "==> Applying database migrations..."
python manage.py migrate --noinput

echo "==> Build complete."