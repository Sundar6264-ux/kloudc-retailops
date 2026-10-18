"""The Alembic history is one straight line with a single head.

Several builders added migrations at the same time in the Oct 14 batch (W1 0053, W4 0054,
W5 0055, W2 0056); a second head would make `alembic upgrade head` refuse to run on prod.
"""

import pathlib

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND = pathlib.Path(__file__).resolve().parents[1]


def _scripts() -> ScriptDirectory:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    return ScriptDirectory.from_config(cfg)


def test_single_head():
    heads = _scripts().get_heads()
    assert len(heads) == 1, f"more than one Alembic head: {heads}"


def test_history_is_linear():
    scripts = _scripts()
    for rev in scripts.walk_revisions():
        assert not isinstance(rev.down_revision, (tuple, list)), f"{rev.revision} is a merge point"
        if rev.down_revision is None:
            continue
        children = [r.revision for r in scripts.walk_revisions() if r.down_revision == rev.down_revision]
        assert children == [rev.revision], f"{rev.down_revision} has several children: {children}"


def test_oct14_batch_chain_order():
    scripts = _scripts()
    expected = [("0053", "0052"), ("0054", "0053"), ("0055", "0054"), ("0056", "0055")]
    for revision, down in expected:
        assert scripts.get_revision(revision).down_revision == down, revision


def test_oct15_batch_chain_order():
    # Oct 15: speed 0057 -> notifications 0058 -> tasks 0059 (the head prod upgrades to).
    scripts = _scripts()
    expected = [("0057", "0056"), ("0058", "0057"), ("0059", "0058")]
    for revision, down in expected:
        assert scripts.get_revision(revision).down_revision == down, revision


def test_multistore_m1_chain_order():
    # Multi-store M1: pos-neutral ids 0060 (MT4) -> item vendors 0061 (MT5), the head.
    scripts = _scripts()
    expected = [("0060", "0059"), ("0061", "0060")]
    for revision, down in expected:
        assert scripts.get_revision(revision).down_revision == down, revision
    assert scripts.get_heads() == ["0061"]
