#!/bin/bash
#SBATCH --job-name=convert_accl_logs_to_plots
#SBATCH --mail-user=aadishms@umich.edu
#SBATCH --mail-type=BEGIN,END,FAIL
#SBATCH --account=rallada0
#SBATCH --partition=standard
#SBATCH --nodes=1
#SBATCH --cpus-per-task=1
#SBATCH --mem-per-cpu=8G
#SBATCH --time=5:00:00
#SBATCH --output=/nfs/turbo/umms-rallada/UM\ Lab\ Users/Aadish/FlySWATTER/BATCH_SCRIPTS/logs/%x-%A.log

set -euo pipefail

PROJECT_DIR="/nfs/turbo/umms-rallada/UM Lab Users/Aadish/FlySWATTER"
SCRIPT_PATH="${PROJECT_DIR}/ConvertAcclLogsToPlots.py"
VENV_DIR="${PROJECT_DIR}/.venv-py310"
DEFAULT_INPUT_DIR="/nfs/turbo/umms-rallada/UM Lab Users/Farheen/Arousal Experiments/R24_R85_RUN3/03-02-2026 T-1.01pm"

if [[ $# -gt 0 ]]; then
  INPUT_DIR="$1"
  shift
else
  INPUT_DIR="${DEFAULT_INPUT_DIR}"
fi

if [[ ! -f "${SCRIPT_PATH}" ]]; then
  echo "Error: script not found: ${SCRIPT_PATH}"
  exit 1
fi

if [[ ! -d "${INPUT_DIR}" ]]; then
  echo "Error: input directory not found: ${INPUT_DIR}"
  exit 1
fi

if [[ ! -d "${VENV_DIR}" ]]; then
  echo "Error: virtual environment not found: ${VENV_DIR}"
  exit 1
fi

if ! type module >/dev/null 2>&1; then
  source /etc/profile.d/modules.sh
fi
module purge
module load python/3.10.4

source "${VENV_DIR}/bin/activate"

echo "[$(date)] Starting ConvertAcclLogsToPlots on ${INPUT_DIR}"
python "${SCRIPT_PATH}" "${INPUT_DIR}" "$@"
echo "[$(date)] Finished ConvertAcclLogsToPlots"
