FROM python:3.12-slim AS runtime

WORKDIR /app

# Use Tsinghua mirror to avoid PyPI timeouts in mainland China
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

COPY pyproject.toml .
COPY src/ src/
COPY plugins/ plugins/
RUN pip install --no-cache-dir setuptools wheel
RUN pip install --no-cache-dir --no-build-isolation .

ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "src.main"]

FROM runtime AS test

RUN pip install --no-cache-dir pytest pytest-asyncio
COPY config/ config/
COPY tests/ tests/
COPY scripts/ scripts/

ENV PYTHONPATH=/app
CMD ["python", "scripts/run_e2e.py", "--levels", "L0,L1,L2,L3,L4,L5"]
