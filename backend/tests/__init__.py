import os

# Old test settings from before the KloudC RetailOps rename (STOREOPS_TEST_PG_URL,
# STOREOPS_TEST_DB, ...) keep working: copied to the RETAILOPS_ names unless set
# (same rule as app/legacy_env.py; app is not imported this early).
for _key in [k for k in os.environ if k.startswith("STOREOPS_")]:
    os.environ.setdefault("RETAILOPS_" + _key[len("STOREOPS_"):], os.environ[_key])
