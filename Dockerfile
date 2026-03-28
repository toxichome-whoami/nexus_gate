FROM python:3.11-slim as builder

WORKDIR /app
COPY requirements.txt .
# Install base deps and build tools
RUN apt-get update && apt-get install -y gcc g++ libffi-dev unixodbc-dev && \
    pip wheel --no-cache-dir --no-deps --wheel-dir /app/wheels -r requirements.txt

FROM python:3.11-slim
WORKDIR /app

# Copy python dependencies
COPY --from=builder /app/wheels /wheels
COPY --from=builder /app/requirements.txt .
RUN pip install --no-cache /wheels/*

COPY . /app

EXPOSE 4500

VOLUME ["/config.toml", "/storage", "/logs", "/data"]

CMD ["python", "-m", "nexusgate", "--config", "/config.toml"]
