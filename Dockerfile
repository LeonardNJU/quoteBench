# Reproducible GNU runner for untrusted QuoteBench command execution.
# The multi-architecture base manifest is pinned. Architecture-specific package
# revisions are recorded into the image and uploaded by CI.
# Build: docker build -t quotebench-runner .
FROM debian:stable-slim@sha256:328d16499860ae6cb9b345e2e4cebca08c2a36e4f7278482c7bd1f39d71e5bfd

LABEL org.opencontainers.image.title="QuoteBench GNU runner" \
      org.opencontainers.image.description="Network-disabled execution image for QuoteBench" \
      org.opencontainers.image.base.digest="sha256:328d16499860ae6cb9b345e2e4cebca08c2a36e4f7278482c7bd1f39d71e5bfd"

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash coreutils findutils gawk git grep sed \
    && dpkg-query -W bash coreutils findutils gawk git grep sed \
       > /usr/local/share/quotebench-runner-versions.txt \
    && rm -rf /var/lib/apt/lists/*
