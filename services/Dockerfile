FROM python:3.12-slim

WORKDIR /app
ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1

COPY services/requirements.txt services/requirements.txt
RUN pip install --no-cache-dir -r services/requirements.txt

COPY services/glassbox services/glassbox

EXPOSE 8000
CMD ["uvicorn", "services.glassbox.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
