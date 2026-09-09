FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY pyproject.toml README.md LICENSE ./
COPY sahara ./sahara
COPY scripts ./scripts
RUN pip install --no-cache-dir .
ENV PORT=8080 SAHARA_DATA_DIR=/data
VOLUME ["/data"]
EXPOSE 8080
CMD ["sh", "-c", "uvicorn sahara.web.app:app --host 0.0.0.0 --port ${PORT}"]
