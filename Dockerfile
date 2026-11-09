FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY src/ ./src/
ENV PYTHONPATH=/app/src
RUN useradd -r -u 10001 app
USER app
# Cloud Run sets $PORT (default 8080)
CMD ["python", "-m", "trazmo_odoo_mcp"]
