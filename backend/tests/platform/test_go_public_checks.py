"""check-config's go-public warnings that need the platform database
(pre-production 2026-10-21)."""

from tests.platform.conftest import provision as provision_store


def test_check_config_names_toast_stores_without_analytics_keys(pdb):
    """check-config's TOAST_ANALYTICS warning reads the saved Toast connection
    (pre-production 2026-10-21: the pilot store had none)."""
    from app.ops.prod_checks import _toast_stores_without_analytics
    from app.platform.credentials import save_credential

    provision_store("ALPHA")
    provision_store("BRAVO", owner="bob", password="bravo-owner-pass")
    base = {"client_id": "cid", "restaurant_guid": "rg"}
    save_credential("ALPHA", "toast", base, {"client_secret": "s"}, actor="t", actor_type="system")
    save_credential("BRAVO", "toast", dict(base, analytics_client_id="aid"),
                    {"client_secret": "s", "analytics_client_secret": "as"}, actor="t", actor_type="system")
    assert _toast_stores_without_analytics() == ["ALPHA"]
