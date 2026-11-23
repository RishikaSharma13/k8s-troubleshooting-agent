# One image for every backend component: collector, watcher, API, exporter, dashboard, migrations.
# The Helm chart picks the command per component. Build from the repository root:
#   docker build -t k8s-failure-agent:dev .
#
# Python 3.10 matches the version the test suite runs on. kubectl is pinned and checksum-verified
# (the collector and the executor call it). The final image has no compilers, no test tools, and runs as non-root.

# ---------- build: install the Python dependencies into a virtual environment ----------
FROM python:3.10-slim-bookworm AS build
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
COPY backend/requirements.txt /tmp/requirements.txt
RUN /opt/venv/bin/pip install -r /tmp/requirements.txt

# ---------- kubectl: pinned version, verified against its published SHA-256 ----------
FROM python:3.10-slim-bookworm AS kubectl
ARG KUBECTL_VERSION=v1.34.13
ARG TARGETARCH=amd64
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
# curl and ca-certificates are only used in this throwaway stage to download and verify kubectl; none of it reaches
# the final image, and pinning Debian package versions breaks when the mirrors drop old versions.
# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/* \
 && curl -fsSLo /tmp/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl" \
 && curl -fsSLo /tmp/kubectl.sha256 "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl.sha256" \
 && echo "$(cat /tmp/kubectl.sha256)  /tmp/kubectl" | sha256sum --check --strict \
 && install -m 0755 /tmp/kubectl /usr/local/bin/kubectl

# ---------- runtime ----------
FROM python:3.10-slim-bookworm
LABEL org.opencontainers.image.title="k8s-failure-agent" \
      org.opencontainers.image.description="Kubernetes failure prediction, safe remediation and verification" \
      org.opencontainers.image.source="https://github.com/RishikaSharma13/k8s-troubleshooting-agent"
ENV PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app/backend \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    MPLCONFIGDIR=/tmp/matplotlib
RUN groupadd --system --gid 10001 app && useradd --system --uid 10001 --gid app --no-create-home app
COPY --from=build /opt/venv /opt/venv
COPY --from=kubectl /usr/local/bin/kubectl /usr/local/bin/kubectl
WORKDIR /app
COPY alembic.ini ./
COPY alembic ./alembic
COPY backend/app ./backend/app
COPY docs/results ./docs/results
USER 10001:10001
EXPOSE 8000 9105
# Default: the API. In the cluster it listens inside the pod only; the chart exposes it as a ClusterIP Service.
CMD ["uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000"]
