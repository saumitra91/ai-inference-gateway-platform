# llama.cpp runtime — prebuilt GPU-enabled binaries from GitHub releases.
#
# Uses Vulkan-backend binaries for NVIDIA GPU support via the NVIDIA
# container runtime.  Vulkan performs within ~5-10 % of native CUDA
# on NVIDIA hardware and is the only GPU-backend the upstream project
# publishes prebuilt Linux tarballs for.
#
# Architecture mapping (Docker TARGETARCH from BuildKit):
#   arm64  → llama-<tag>-bin-ubuntu-vulkan-arm64.tar.gz
#   amd64  → llama-<tag>-bin-ubuntu-vulkan-x64.tar.gz

FROM ubuntu:24.04

ARG LLAMA_CPP_RELEASE=b9263
ARG TARGETARCH

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    tar \
    findutils \
    libgomp1 \
    libvulkan1 \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    case "${TARGETARCH}" in \
      arm64) SUFFIX="ubuntu-vulkan-arm64" ;; \
      amd64) SUFFIX="ubuntu-vulkan-x64" ;; \
      *) echo "unsupported TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    URL="https://github.com/ggml-org/llama.cpp/releases/download/${LLAMA_CPP_RELEASE}/llama-${LLAMA_CPP_RELEASE}-bin-${SUFFIX}.tar.gz"; \
    curl -fL "${URL}" -o /tmp/llama.tgz; \
    mkdir -p /opt/llama; \
    tar -xzf /tmp/llama.tgz -C /opt/llama --strip-components=1; \
    ln -s /opt/llama/llama-server /usr/local/bin/llama-server; \
    chmod +x /opt/llama/llama-server; \
    rm -f /tmp/llama.tgz

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
ENV LD_LIBRARY_PATH=/usr/local/lib/llama
USER llama

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=5 \
    CMD curl -sf "http://127.0.0.1:${PORT}/health" || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
