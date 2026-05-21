#!/usr/bin/env sh
# ---------------------------------------------------------------------------
# vLLM entrypoint
#
# Responsibilities:
#   1. Validate the model directory exists before starting
#   2. Log startup context in structured key=value format
#   3. Exec vLLM's OpenAI-compatible API server so PID 1 is the server
#
# Environment variables:
#   VLLM_MODEL_PATH  – path to the HuggingFace model directory or model ID
#   VLLM_DEVICE      – "cuda" (default) or "cpu"
#   VLLM_PORT        – listen port (default 8000)
#   VLLM_HOST        – listen address (default 0.0.0.0)
#   VLLM_DTYPE       – model dtype (default auto)
#   VLLM_MAX_MODEL_LEN – max model context length (default 4096)
#   VLLM_GPU_MEMORY_UTIL – GPU memory utilization 0-1 (default 0.90)
#   VLLM_EXTRA_ARGS  – additional flags appended verbatim
# ---------------------------------------------------------------------------
set -eu

log() {
    printf 'level=info service=vllm %s\n' "$*"
}

log_error() {
    printf 'level=error service=vllm %s\n' "$*" >&2
}

# ---------------------------------------------------------------------------
# 0. Architecture / runtime visibility
# ---------------------------------------------------------------------------
log "msg=startup_probe uname_m=$(uname -m) uname_s=$(uname -s)"

# ---------------------------------------------------------------------------
# 1. Defaults
# ---------------------------------------------------------------------------
VLLM_MODEL_PATH="${VLLM_MODEL_PATH:-Qwen/Qwen2.5-3B-Instruct}"
VLLM_DEVICE="${VLLM_DEVICE:-cuda}"
VLLM_HOST="${VLLM_HOST:-0.0.0.0}"
VLLM_PORT="${VLLM_PORT:-8000}"
VLLM_DTYPE="${VLLM_DTYPE:-auto}"
VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-4096}"
VLLM_GPU_MEMORY_UTIL="${VLLM_GPU_MEMORY_UTIL:-0.90}"
VLLM_EXTRA_ARGS="${VLLM_EXTRA_ARGS:-}"

# ---------------------------------------------------------------------------
# 2. Model validation
# ---------------------------------------------------------------------------
# Detect whether VLLM_MODEL_PATH is a HuggingFace repo ID or a local path
case "${VLLM_MODEL_PATH}" in
    /*|./*|../*)
        _IS_LOCAL=1
        ;;
    *)
        _IS_LOCAL=0
        ;;
esac

if [ "${VLLM_DEVICE}" = "cpu" ] && [ "${_IS_LOCAL}" = "1" ]; then
    if [ ! -d "${VLLM_MODEL_PATH}" ]; then
        log_error "msg=model_dir_not_found path=${VLLM_MODEL_PATH}"
        log_error "msg=hint mount a HuggingFace model directory at ${VLLM_MODEL_PATH}"
        log_error "msg=hint example: place model files in ./models/vllm/ on the host"
        exit 1
    fi
    # Directory exists — check non-empty
    if [ -z "$(ls -A "${VLLM_MODEL_PATH}" 2>/dev/null)" ]; then
        log_error "msg=model_dir_empty path=${VLLM_MODEL_PATH}"
        log_error "msg=hint the directory exists but contains no model files"
        log_error "msg=hint download a model: git lfs clone https://huggingface.co/Qwen/Qwen2.5-3B-Instruct ./models/vllm/"
        exit 1
    fi
fi
# GPU-only: fail if CUDA is not available
if [ "${VLLM_DEVICE}" = "cuda" ]; then
    log "msg=checking_cuda"
    if ! "${_PYTHON}" -c "import torch; assert torch.cuda.is_available(), 'CUDA not available'"; then
        log_error "msg=cuda_not_available"
        log_error "msg=hint ensure NVIDIA Container Toolkit is installed and GPUs are accessible"
        log_error "msg=hint run: sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker"
        exit 1
    fi
    log "msg=cuda_ok"
fi

log "msg=model_found path=${VLLM_MODEL_PATH} device=${VLLM_DEVICE}"

# ---------------------------------------------------------------------------
# 3. Startup context log
# ---------------------------------------------------------------------------
log "msg=starting model_path=${VLLM_MODEL_PATH} device=${VLLM_DEVICE} host=${VLLM_HOST} port=${VLLM_PORT} dtype=${VLLM_DTYPE} max_model_len=${VLLM_MAX_MODEL_LEN} gpu_memory_util=${VLLM_GPU_MEMORY_UTIL} extra_args=${VLLM_EXTRA_ARGS}"

# ---------------------------------------------------------------------------
# 4. Build command and exec
# ---------------------------------------------------------------------------
# Find the correct python binary (official vLLM image may use /opt/venv)
_PYTHON=""
for _bin in python3 python /opt/venv/bin/python3 /usr/local/bin/python3; do
    if command -v "${_bin}" >/dev/null 2>&1; then
        _PYTHON="${_bin}"
        break
    fi
done
if [ -z "${_PYTHON}" ]; then
    log_error "msg=python_not_found"
    exit 1
fi

# ── Build the args array (handles empty vars gracefully) ─────────────────
set -- \
    "${_PYTHON}" -m vllm.entrypoints.openai.api_server \
    --model "${VLLM_MODEL_PATH}" \
    --host "${VLLM_HOST}" \
    --port "${VLLM_PORT}" \
    --dtype "${VLLM_DTYPE}" \
    --max-model-len "${VLLM_MAX_MODEL_LEN}" \
    --served-model-name vllm-model

if [ -n "${VLLM_GPU_MEMORY_UTIL}" ] && [ "${VLLM_DEVICE}" = "cuda" ]; then
    set -- "$@" --gpu-memory-utilization "${VLLM_GPU_MEMORY_UTIL}"
fi

# shellcheck disable=SC2086
exec "$@" ${VLLM_EXTRA_ARGS}
