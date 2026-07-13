# Runner image for `--executor docker` (recommended when evaluating untrusted
# model output). Build:  docker build -t quotebench-runner .
FROM debian:stable-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    git gawk grep sed findutils coreutils \
    && rm -rf /var/lib/apt/lists/*
