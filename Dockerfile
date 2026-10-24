FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src/ src/
COPY app/ app/
COPY data/ data/
COPY models/ models/

RUN useradd --create-home --uid 1000 authshield
USER authshield

# 8501: dashboard (por defecto) · 8000: API (uvicorn app.api:app)
EXPOSE 8501 8000
# Sano si responde el modo con el que se arrancó: dashboard o API.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
    CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8501/_stcore/health')" \
     || python -c "import urllib.request as u; u.urlopen('http://localhost:8000/health')"

CMD ["streamlit", "run", "app/dashboard.py", "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
