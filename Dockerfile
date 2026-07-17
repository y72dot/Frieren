# Dockerfile for QQ Bot
FROM python:3.12-slim

WORKDIR /app

# Install Python deps (source will be volume-mounted in dev)
COPY pyproject.toml .
RUN pip install --no-cache-dir napcat-sdk loguru python-dotenv aiofiles

COPY src/ src/
COPY plugins/ plugins/
COPY config/ config/

CMD ["python", "-m", "src.main"]
