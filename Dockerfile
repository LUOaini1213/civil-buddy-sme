# Civil Buddy packing gateway + frontend (cloud-ready)
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PACKING_OUTPUT_DIR=/app/output \
    PACKING_TRACE_DIR=/app/output/traces \
    PACKING_SKIP_SKJOLBER=1 \
    CB_DB_PATH=/app/output/db/civilbuddy.db \
    PORT=8000

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir fastapi "uvicorn[standard]" python-dotenv openpyxl python-multipart

# Everything the gateway reads at startup and per request (contract/, workbench/seed.json,
# demo/kb, knowledge_base/, examples/ ...). .dockerignore keeps out .git, venvs, output/,
# databases, .env*, *.local.json, keys and build dirs.
COPY . .

RUN mkdir -p /app/output/runs /app/output/traces /app/output/db

EXPOSE 8000
# Cloud platforms inject $PORT — bind 0.0.0.0 so public URL works; without CIVIL_TOKEN the gateway refuses to start.
# --no-access-log: uvicorn logs the path with its query string, so the one-time ?token= link would sit in
# `docker logs`; a proxy in front (deploy/lightsail/Caddyfile) keeps an access log with the token filtered out.
CMD ["sh", "-c", "python -m uvicorn gateway.app:app --host 0.0.0.0 --port ${PORT:-8000} --no-access-log"]
