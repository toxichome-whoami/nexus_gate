# ─────────────────────────────────────────────────────────────────────────────
# Stage 1: Dependency Compilation (Builder)
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim as builder

WORKDIR /app

# Enable system-level build dependencies for native extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libffi-dev \
    unixodbc-dev \
    && rm -rf /var/lib/apt/lists/*

# Optimize pip wheel caching and artifact generation
COPY requirements.txt .
RUN pip wheel --no-cache-dir --no-deps --wheel-dir /app/wheels -r requirements.txt

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2: Production Runtime
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim

LABEL maintainer="NexusGate Team"
LABEL description="Industrial-grade unified API gateway for databases and file storage."

WORKDIR /app

# Import pre-compiled dependencies from the builder stage
COPY --from=builder /app/wheels /wheels
COPY --from=builder /app/requirements.txt .
RUN pip install --no-cache /wheels/*

# Import application source and static configuration
COPY . /app

# Standard communication port for the API gateway
EXPOSE 4500

# Persistent volume definitions for stateful storage
VOLUME ["/config.toml", "/storage", "/logs", "/data"]

# Bootstrap the NexusGate service
CMD ["python", "-m", "nexusgate", "--config", "/config.toml"]
