# Settings shared by the regression scripts (sourced, zsh or bash).
#
# REG_WORK  scratch folder: the prod COPY, the built website, screenshots, logs.
#           Never inside the repo. Default: $TMPDIR/retailops-suiteA.
# REG_PORT  the throwaway server's port (a mock Toast runs on REG_PORT + 10000).
# The caller sets HERE (this folder) before sourcing.
REG_REPO="$(cd "$HERE/../../.." && pwd)"
: "${REG_WORK:=${TMPDIR:-/tmp}/retailops-suiteA}"
: "${REG_PORT:=8830}"
REG_MOCK_PORT=$((REG_PORT + 10000))
# The store database to copy. Only ever opened read-only (mode=ro) to take a
# .backup (prepare.sh). No default: set REG_PROD_DB before prepare.sh.
: "${REG_PROD_DB:=}"
REG_PY="$REG_REPO/backend/.venv/bin/python"
export REG_REPO REG_WORK REG_PORT REG_MOCK_PORT REG_PROD_DB REG_PY
