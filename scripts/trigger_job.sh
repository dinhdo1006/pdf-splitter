#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
JOB_ID="${1:?usage: trigger_job.sh JOB_ID [extra minio_run args...]}"
cd "$ROOT"
source .venv/bin/activate
exec python minio_trigger.py "$JOB_ID" "${@:2}"
