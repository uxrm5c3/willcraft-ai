FROM python:3.11-slim

# WeasyPrint system dependencies + LibreOffice for docx->pdf
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libpangoft2-1.0-0 \
    libgdk-pixbuf-2.0-0 libffi-dev libcairo2 \
    libglib2.0-0 shared-mime-info \
    libreoffice-writer-nogui \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

# Create data directory for SQLite and uploads
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "120", "app:app"]
