"""The independent checks of the published site must pass on what is committed."""

from __future__ import annotations

import pytest

pytest.importorskip("scipy")

from src import verify  # noqa: E402


@pytest.fixture(scope="module")
def report() -> dict:
    return verify.run()


def test_every_check_ran(report):
    assert report["total"] == len(verify.CHECKS)
    assert all(c["detail"] for c in report["checks"])


# The accuracy file and the forecast log are both rewritten by every pipeline
# run, so that check is only meaningful right after one; the pipeline runs it.
@pytest.mark.parametrize("check_id", [c[0] for c in verify.CHECKS if c[0] != "accuracy"])
def test_check_passes(report, check_id):
    check = next(c for c in report["checks"] if c["id"] == check_id)
    assert check["ok"], check["detail"]


def test_linear_programme_agrees_with_a_case_solved_by_hand():
    import numpy as np

    # Two hours, buy at 1 and sell at 3, no losses: one kWh moved earns 2.
    net = np.zeros(2)
    bill = verify._lp_bill(net, np.array([1.0, 3.0]), np.array([1.0, 3.0]), 1.0, 1.0, 1.0, 0.0)
    assert bill == pytest.approx(-2.0)
