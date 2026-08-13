FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY demo ./demo
COPY benchmark ./benchmark

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "impact_engine.main:app", "--host", "0.0.0.0", "--port", "8000"]