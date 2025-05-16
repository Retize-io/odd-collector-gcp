# Build stage: install dependencies and compile bytecode
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build

# Set up workdir
WORKDIR /app

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy from the cache instead of linking
ENV UV_LINK_MODE=copy

# Install the project's dependencies
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

# Copy project source and install the application
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# Runtime stage: create a slimmer image for running the application
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS runtime

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash app
USER app

# Non-interactive env vars
ENV DEBIAN_FRONTEND=noninteractive
ENV DEBCONF_NONINTERACTIVE_SEEN=true
ENV UCF_FORCE_CONFOLD=1
ENV PYTHONUNBUFFERED=1

# Application directory
WORKDIR /app

# Copy application from build stage
COPY --from=build --chown=app:app /app /app

# Add virtual env to path
ENV PATH="/app/.venv/bin:$PATH"

# Run the ODD Collector application
ENTRYPOINT ["bash", "start.sh"]
