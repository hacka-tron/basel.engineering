FROM node:22-alpine AS frontend-build

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
# The portfolio grid is built from corpus/portfolio/*.md (vite-plugins/portfolio.ts
# resolves ../corpus/portfolio from /app/frontend); without it the build fails
# rather than shipping an empty portfolio. Images are in frontend/public/portfolio/.
COPY corpus/portfolio/ /app/corpus/portfolio/
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

# The release tag (build-N) for GET /api/version, which the post-deploy
# stream check polls to see a release go live. Declared last so a new value
# only rebuilds this metadata layer, never the cached layers above.
ARG GLASSBOX_BUILD=dev
ENV GLASSBOX_BUILD=$GLASSBOX_BUILD

USER glassbox
EXPOSE 8000
CMD ["uvicorn", "services.glassbox.api.main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "25"]
