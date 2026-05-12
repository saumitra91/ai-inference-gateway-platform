#!/usr/bin/env sh
# ---------------------------------------------------------------------------
# llama-server entrypoint
#
# Responsibilities:
#   1. Validate the model file exists before attempting to start
#   2. Log startup context in structured key=value format (parseable by
#      any log aggregator without a special parser)
#   3. Exec llama-server so PID 1 is the actual server process, not this
#      shell — this means SIGTERM/SIGINT reach llama-server directly and
#      Docker's graceful shutdown window is honoured properly
#
# Environment variables (with defaults set in Dockerfile):
#   MODEL_PATH      – absolute path to the GGUF model file
#   CONTEXT_SIZE    – KV cache context window (-c)
#   NUM_THREADS     – CPU threads to use   (--threads)
#   HOST            – listen address       (--host)
#   PORT            – listen port          (--port)
#   EXTRA_ARGS      – any additional flags appended verbatim to the command
# ---------------------------------------------------------------------------
set -eu
export LD_LIBRARY_PATH=/opt/llama:$LD_LIBRARY_PATH
export GGML_BACKEND_PATH=/opt/llama

log() {
    # Print structured key=value log line to stdout so Docker's log driver
    # picks it up with correct timestamps. This matches the style used by
    # the Django JSON logger so log aggregators can corelate across services.
    printf 'level=info service=llamacpp %s\n' "$*"
}

log_error() {
    printf 'level=error service=llamacpp %s\n' "$*" >&2
}

# ---------------------------------------------------------------------------
# 0. Architecture / runtime visibility (helps debug ARM vs x86 mixups)
# ---------------------------------------------------------------------------
log "msg=startup_probe uname_m=$(uname -m) uname_s=$(uname -s) num_threads_cfg=${NUM_THREADS}"

# ---------------------------------------------------------------------------
# 1. Thread count auto-detection
#    If NUM_THREADS is 0 or "auto", detect available CPU count and apply a
#    conservative heuristic: leave one core for the OS / Django process.
#    This avoids the common misconfiguration where the user sets threads
#    higher than the core count, causing context-switching overhead.
# ---------------------------------------------------------------------------
if [ "${NUM_THREADS}" = "0" ] || [ "${NUM_THREADS}" = "auto" ]; then
    _cores="$(nproc 2>/dev/null || sysctl -n hw.logicalcpu 2>/dev/null || echo 2)"
    NUM_THREADS="$(( _cores > 2 ? _cores - 1 : _cores ))"
    log "msg=auto_thread_detect cores=${_cores} num_threads=${NUM_THREADS}"
fi

# ---------------------------------------------------------------------------
# 2. Model validation
#    llama-server produces a cryptic segfault or a misleading mmap error
#    when the model file is absent. Checking early gives a clear error and
#    exits with a non-zero code so Docker marks the container as failed
#    rather than running a zombie process.
# ---------------------------------------------------------------------------
if [ ! -f "${MODEL_PATH}" ]; then
    log_error "msg=model_not_found path=${MODEL_PATH}"
    log_error "msg=hint mount a GGUF file at ${MODEL_PATH} or set MODEL_PATH to the correct path"
    log_error "msg=hint see models/README.txt for instructions"
    exit 1
fi

# Basic sanity: GGUF magic bytes are "GGUF" (0x47475546) at offset 0
_magic="$(dd if="${MODEL_PATH}" bs=1 count=4 2>/dev/null | od -A n -t x1 | tr -d ' \n')"
if [ "${_magic}" != "47475546" ]; then
    log_error "msg=model_invalid_magic path=${MODEL_PATH} magic=${_magic} expected=47475546"
    log_error "msg=hint the file is not a valid GGUF model"
    exit 1
fi

# ---------------------------------------------------------------------------
# 3. Startup context log
# ---------------------------------------------------------------------------
log "msg=starting model_path=${MODEL_PATH} context_size=${CONTEXT_SIZE} num_threads=${NUM_THREADS} host=${HOST} port=${PORT} extra_args=${EXTRA_ARGS}"

# ---------------------------------------------------------------------------
# 4. exec (not sh -c) so llama-server is PID 1
# ---------------------------------------------------------------------------
# Word-splitting on EXTRA_ARGS is intentional here: the variable is meant to
# carry additional CLI flags like "--no-mmap --flash-attn" and needs to be
# split on spaces.
#
# shellcheck disable=SC2086
exec llama-server \
  --jinja \
  --model "${MODEL_PATH}" \
  --host "${HOST}" \
  --port "${PORT}" \
  --ctx-size "${CONTEXT_SIZE}" \
  --threads "${NUM_THREADS}" \
  ${EXTRA_ARGS}
