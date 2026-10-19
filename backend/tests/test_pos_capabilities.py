"""The shared POS capability list (multi-store plan 4.5)."""

from app.pos.capabilities import TOAST_CAPABILITIES, Capability


def test_seventeen_flags_with_stable_values():
    assert len(Capability) == 17
    for cap in Capability:
        # The string value is what the app and the database see; it is the lower-case name.
        assert cap.value == cap.name.lower()
        assert Capability(cap.value) is cap
        assert cap == cap.value  # str Enum: compares equal to its value


def test_toast_set_is_what_main_store_has_today():
    assert isinstance(TOAST_CAPABILITIES, frozenset)
    assert TOAST_CAPABILITIES <= set(Capability)
    missing = set(Capability) - TOAST_CAPABILITIES
    assert missing == {
        Capability.ORDERS_INCREMENTAL,
        Capability.STOCK_NEGATIVE_COUNTS,
        Capability.ITEM_VENDORS,
        Capability.ITEM_COST,
    }
