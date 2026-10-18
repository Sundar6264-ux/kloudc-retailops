"""Which tenancy mode this process runs in (docs/PLATFORM_CONTRACT.md 0.2.3)."""


def is_multi_tenant() -> bool:
    """TENANCY_MODE == "multi" (the shared service). Read at call time from
    the process settings, so a test can switch it."""
    from app.config import process_settings

    return process_settings().tenancy_mode == "multi"
