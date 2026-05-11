# llama.cpp runtime — prebuilt CPU binaries from GitHub releases (no source compile).
#
# Why prebuilts in Docker (especially on Apple Silicon)?
#   • Source builds with GGML_NATIVE / aggressive SIMD flags have repeatedly
#     broken under QEMU/virtio and mixed ARM feature levels. Official release
#     tarballs are built on stable Ubuntu CI matrices and are the lowest-friction
#     path for reproducible local + Hetzner amd64 deployments.
#   • The release tag (LLAMA_CPP_RELEASE) is immutable; the download URL is
#     deterministic for CI caches and security review.
#
# Architecture mapping (Docker TARGETARCH from BuildKit):
#   arm64  → llama-<tag>-bin-ubuntu-arm64.tar.gz   (Linux/arm64 — Docker Desktop on Mac)
#   amd64  → llama-<tag>-bin-ubuntu-x64.tar.gz     (Linux/x86_64 — Hetzner, etc.)

FROM ubuntu:24.04

ARG LLAMA_CPP_RELEASE=b9106
ARG TARGETARCH

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    tar \
    findutils \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    case "${TARGETARCH}" in \
      arm64) SUFFIX="ubuntu-arm64" ;; \
      amd64) SUFFIX="ubuntu-x64" ;; \
      *) echo "unsupported TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    URL="https://github.com/ggml-org/llama.cpp/releases/download/${LLAMA_CPP_RELEASE}/llama-${LLAMA_CPP_RELEASE}-bin-${SUFFIX}.tar.gz"; \
    echo "level=info service=llamacpp_build msg=downloading url=${URL}"; \
    curl -fL "${URL}" -o /tmp/llama.tgz; \
    mkdir -p /tmp/extract; \
    tar -xzf /tmp/llama.tgz -C /tmp/extract; \
    EXTRACT_DIR="$(find /tmp/extract -maxdepth 1 -type d -name 'llama-*' | head -n1)"; \
    test -n "${EXTRACT_DIR}"; \
    cp "${EXTRACT_DIR}/llama-server" /usr/local/bin/llama-server; \
    chmod +x /usr/local/bin/llama-server; \
    find "${EXTRACT_DIR}" -name '*.so*' -exec cp {} /usr/local/lib/ \; ; \
    ldconfig; \
    rm -rf /tmp/extract /tmp/llama.tgz; \
    /usr/local/bin/llama-server --version

RUN useradd -m -u 1001 -s /bin/bash llama

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

RUN mkdir -p /models && chown llama:llama /models
VOLUME ["/models"]

ENV MODEL_PATH=/models/model.gguf \
    CONTEXT_SIZE=4096 \
    NUM_THREADS=4 \
    HOST=0.0.0.0 \
    PORT=8080 \
    EXTRA_ARGS=""

EXPOSE 8080

USER llama

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=5 \
    CMD curl -sf "http://127.0.0.1:${PORT}/health" || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
