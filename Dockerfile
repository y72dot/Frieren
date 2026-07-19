FROM python:3.12-slim

WORKDIR /app

# Use Tsinghua mirror to avoid PyPI timeouts in mainland China
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

COPY pyproject.toml .
RUN pip install --no-cache-dir setuptools wheel
RUN pip install --no-cache-dir --no-build-isolation .

COPY src/ src/
COPY plugins/ plugins/

ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "src.main"]
