FROM node:22-alpine AS frontend-build

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS runtime

WORKDIR /app
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

COPY services/requirements.txt services/requirements.txt
RUN pip install --no-cache-dir -r services/requirements.txt \
    && groupadd --system glassbox \
    && useradd --system --gid glassbox --home-dir /app glassbox

COPY alembic.ini ./
COPY services/ services/
COPY corpus/ corpus/
COPY docs/ docs/
COPY infra/ infra/
COPY k8s/ k8s/
COPY --from=frontend-build /app/frontend/dist frontend/dist
# The answer-cache warm-up (services/glassbox/warm.py) reads the same
# suggested questions the frontend shows.
COPY frontend/src/suggested-questions.json frontend/src/suggested-questions.json

USER glassbox
EXPOSE 8000
CMD ["uvicorn", "services.glassbox.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "25"]
