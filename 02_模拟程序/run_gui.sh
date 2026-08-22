#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "${PYTHONPATH:-}" ]]; then
  export PYTHONPATH="${project_root}/src:${PYTHONPATH}"
else
  export PYTHONPATH="${project_root}/src"
fi

python_executable="${AIRVORTEX_PYTHON:-}"
if [[ -z "${python_executable}" ]]; then
  gpu_python="${project_root}/.venv-gpu/bin/python"
  if [[ -x "${gpu_python}" ]] && "${gpu_python}" -c \
    'from airvortex.numerics import probe_gpu_projection; raise SystemExit(0 if probe_gpu_projection().available else 1)' \
    >/dev/null 2>&1; then
    python_executable="${gpu_python}"
  else
    python_executable="python3"
  fi
fi

exec "${python_executable}" -m airvortex.gui "$@"
