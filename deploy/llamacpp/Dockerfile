# ---------------------------------------------------------------------------
# Stage 1 — builder
# ---------------------------------------------------------------------------
# We compile llama.cpp from source here and produce a standalone binary.
# The builder stage carries the full toolchain (cmake, g++, libc dev) so
# none of that overhead lands in the runtime image.
#
# Why build from source rather than pulling GHCR prebuilts?
#   • GHCR tags disappear, rotate naming schemes, or change entrypoints
#     without notice. A source build pins to an exact commit/tag and gives
#     you a reproducible, auditable artifact.
#   • You own the compile flags. For CPU-only inference on arm64 you want
#     GGML_NATIVE=ON (autovectorise for the host's NEON/SVE) and can omit
#     CUDA/Metal/Vulkan entirely — the prebuilt images enable features that
#     add tens of MB to the image and require runtime libraries that aren't
#     present in a minimal container.
#   • Security: no blind trust in a third-party registry that ships "latest".
# ---------------------------------------------------------------------------

ARG LLAMA_CPP_TAG=b5100
ARG TARGETOS=linux
ARG TARGETARCH=amd64

FROM debian:bookworm-slim AS builder

ARG LLAMA_CPP_TAG
ARG TARGETARCH

# --------------------------------------------------------------------------
# System toolchain
# --------------------------------------------------------------------------
# We need cmake ≥ 3.14, ninja for parallel builds, and a C++17-capable
# compiler. Debian bookworm ships cmake 3.25 which is sufficient.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    cmake \
    ninja-build \
    git \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# --------------------------------------------------------------------------
# Source checkout — pinned tag avoids floating HEAD surprises.
# --------------------------------------------------------------------------
WORKDIR /src
RUN git clone --depth 1 --branch "${LLAMA_CPP_TAG}" \
        https://github.com/ggml-org/llama.cpp.git .

# --------------------------------------------------------------------------
# Compile flags
# --------------------------------------------------------------------------
# GGML_NATIVE: emit ISA-specific code for the build host. In Docker on a
#   modern Mac (arm64) this enables NEON; on x86-64 it enables AVX2/FMA.
#   The tradeoff: the binary is not portable across microarchitectures, but
#   for a container that always runs on the same host kernel this is fine and
#   gives a meaningful throughput boost (~10-30% on AVX2 workloads).
#
# GGML_BLAS: disabled intentionally. OpenBLAS threads and llama.cpp's own
#   thread pool can contend for the same CPU cores; the BLAS path is also
#   harder to reason about under memory pressure. Re-enable if you profile
#   and find matrix multiplications are the bottleneck.
#
# BUILD_SHARED_LIBS=OFF: static link produces a single self-contained binary
#   easy to copy into the runtime stage without worrying about .so paths.
# --------------------------------------------------------------------------
RUN cmake -S . -B build -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
        -DGGML_NATIVE=ON \
        -DGGML_BLAS=OFF \
        -DGGML_CUDA=OFF \
        -DGGML_METAL=OFF \
        -DGGML_VULKAN=OFF \
        -DBUILD_SHARED_LIBS=OFF \
        -DLLAMA_BUILD_SERVER=ON \
        -DLLAMA_BUILD_TESTS=OFF \
        -DLLAMA_BUILD_EXAMPLES=OFF \
    && cmake --build build --target llama-server -j "$(nproc)"

# Collect the binary and sanity-check it runs
RUN ls -lh build/bin/llama-server \
    && build/bin/llama-server --version

# ---------------------------------------------------------------------------
# Stage 2 — runtime
# ---------------------------------------------------------------------------
# Minimal Debian base: no toolchain, no source, no build artefacts.
# The final image is typically 50-80 MB instead of 600+ MB with the build
# stage included.
# ---------------------------------------------------------------------------
FROM debian:bookworm-slim AS runtime

# curl is needed for the healthcheck script
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

RUN adduser --disabled-password --gecos "" --uid 1001 llama

COPY --from=builder /src/build/bin/llama-server /usr/local/bin/llama-server
RUN chmod +x /usr/local/bin/llama-server

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Default model directory (override via MODEL_PATH env var)
RUN mkdir -p /models && chown llama:llama /models

# Declare the models directory as a Docker volume so the host can mount
# GGUF files at runtime. Note: this creates an *anonymous* volume if no
# explicit mount is given — always provide a named volume or bind mount in
# compose to avoid orphaning large model files.
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
