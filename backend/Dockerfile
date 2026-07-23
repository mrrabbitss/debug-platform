FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends cppcheck clang-tidy && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml alembic.ini ./
COPY app ./app
RUN pip install --no-cache-dir .
RUN mkdir -p /app/data/storage
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/ready', timeout=3).read()"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
