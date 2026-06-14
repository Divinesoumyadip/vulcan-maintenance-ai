# VULCAN — production container (v11)
#   UI:        docker run -p 8501:8501 -e ANTHROPIC_API_KEY=... vulcan
#   Autonomy:  docker run -p 8799:8799 vulcan python vulcan_service.py
#   Both, properly separated: docker compose up   (see docker-compose.yml)
FROM python:3.12-slim

# Security: never run as root. The app needs write access only to /app/data.
RUN useradd --create-home --uid 10001 vulcan
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN chown -R vulcan:vulcan /app/data
USER vulcan

EXPOSE 8501 8799
# UI healthcheck; the service container overrides this with /healthz
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8501/_stcore/health')" || exit 1
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
