FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY worker ./worker

RUN useradd --system --uid 10001 appuser
USER appuser

EXPOSE 9100

# Healthy as long as the metrics endpoint answers; deliberately independent of the EDW (target).
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/metrics' % os.getenv('METRICS_PORT', '9100'), timeout=2).status == 200 else 1)"

ENTRYPOINT ["python", "-m", "worker"]
