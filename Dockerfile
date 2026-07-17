FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir napcat-sdk loguru python-dotenv aiofiles

COPY src/ src/
COPY plugins/ plugins/

ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "src.main"]
