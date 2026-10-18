"""Old module names keep working after code moves into ``app.pos`` (M1).

M1 moves the Toast code into ``app.pos.adapters.toast`` without changing
behaviour, and every old import path (``app.toast_client``,
``app.services.toast_analytics``, ``app.services.inventory_api`` ...) keeps
working. A plain ``from new import *`` re-export is not enough here: the tests
(and any future test written against the old path) patch the OLD module, for
example ``monkeypatch.setattr(toast_client, "get_access_token", fake)``, and
expect the moved code to see the fake. A re-export would quietly patch a copy
nobody calls any more.

``install_forwarding`` turns the old module into a view of the new ones for
the moved names:

- reading ``old.NAME`` returns the new module's current ``NAME``;
- setting or deleting ``old.NAME`` (monkeypatch, mock.patch) sets or deletes
  it on the new module, so every caller of the moved code sees it - and, when
  the old module still keeps its own copy of the name because code left
  behind in it calls the name directly, that copy is updated too.

The only gap: patching a moved name on the NEW module does not reach code
left behind in the old module that calls it by bare name. After MT3 that is
only ``inventory_api``'s ``barcode_overlay`` (``merged_barcode``),
``status`` (``live_stock_meta``) and ``compare_now`` (``ToastFetchError``).
Patch the old path in that case, as today's tests do.
"""

import sys
import types
from typing import Dict, Iterable, Mapping


def install_forwarding(module_name: str, forwards: Mapping[str, types.ModuleType]) -> None:
    """Make ``module_name`` forward each name in ``forwards`` to the module
    that now owns it. Safe to call once per module, at import time."""
    module = sys.modules[module_name]
    table: Dict[str, types.ModuleType] = dict(forwards)
    for name, owner in table.items():
        if not hasattr(owner, name):
            raise AttributeError(f"{owner.__name__} has no {name!r} to forward from {module_name}")

    class ForwardingModule(types.ModuleType):
        _pos_forwards = table

        def __getattribute__(self, name):
            owner = table.get(name)
            if owner is not None:
                return getattr(owner, name)
            return types.ModuleType.__getattribute__(self, name)

        def __setattr__(self, name, value):
            owner = table.get(name)
            if owner is None:
                types.ModuleType.__setattr__(self, name, value)
                return
            setattr(owner, name, value)
            # Code left behind in the old module calls its own copy by bare name.
            own = types.ModuleType.__getattribute__(self, "__dict__")
            if name in own:
                own[name] = value

        def __delattr__(self, name):
            owner = table.get(name)
            if owner is None:
                types.ModuleType.__delattr__(self, name)
                return
            delattr(owner, name)
            own = types.ModuleType.__getattribute__(self, "__dict__")
            own.pop(name, None)

        def __dir__(self):
            own = types.ModuleType.__getattribute__(self, "__dict__")
            return sorted(set(own) | set(table))

    ForwardingModule.__name__ = ForwardingModule.__qualname__ = "ForwardingModule"
    module.__class__ = ForwardingModule


def forwards_for(owner: types.ModuleType, names: Iterable[str]) -> Dict[str, types.ModuleType]:
    """{name: owner} for each name (helper for building the table)."""
    return {name: owner for name in names}


def forwarded_names(module: types.ModuleType) -> Dict[str, str]:
    """{name: owning module name} for a module set up by install_forwarding
    (empty for any other module). Used by the tests."""
    table = getattr(type(module), "_pos_forwards", None) or {}
    return {name: owner.__name__ for name, owner in table.items()}


def defined_names(owner: types.ModuleType) -> list:
    """Every global a module holds except dunders and imported modules - for a
    module moved whole, so the old name forwards everything it used to hold
    (functions, classes, constants, locks, caches, test seams, and imported
    helpers such as ``get_settings``, which a test may patch on the old path)."""
    return sorted(
        name
        for name, value in vars(owner).items()
        if not (name.startswith("__") and name.endswith("__")) and not isinstance(value, types.ModuleType)
    )

