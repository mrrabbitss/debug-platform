FROM python:3.14-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends cppcheck clang-tidy && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock constraints.lock alembic.ini ./
COPY app ./app
RUN python -m pip install --no-cache-dir --upgrade --constraint constraints.lock pip \
    && python -m pip install --no-cache-dir --constraint constraints.lock .
RUN mkdir -p /app/data/storage
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/ready', timeout=3).read()"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
