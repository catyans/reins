import pytest

from reins.policies import PolicyBundle, assess_holdout, learn_bundle


def reports():
    rows = [
        dict(
            id=str(i),
            segment="short:ordinary",
            success=True,
            cost=0.1,
            cost_complete=True,
            total_ms=10,
        )
        for i in range(40)
    ]
    return {"base": rows, "cheap": [r | {"cost": 0.01} for r in rows]}


def learn(r, **kw):
    return learn_bundle(
        r,
        {"base": {"model": "strong"}, "cheap": {"model": "lite"}},
        task_type="extract",
        baseline="base",
        evaluator_version="exact-v1",
        **kw,
    )


def test_frozen_bundle_and_no_test_selection(tmp_path):
    b = learn(reports())
    assert b.choose({"source": "hi"}) == ("cheap", "validated_segment")
    assert b.choose({"source": "hi", "source_conflict": True})[0] == "base"
    p = tmp_path / "bundle.json"
    b.save(p)
    assert PolicyBundle.load(p) == b
    p.write_text(p.read_text().replace("strong", "other"))
    with pytest.raises(ValueError):
        PolicyBundle.load(p)
    with pytest.raises(ValueError):
        learn(reports(), split="test")


@pytest.mark.parametrize(
    "mutation",
    [{"success": False}, {"cost_complete": False}, {"cost": float("inf")}, {"total_ms": 100000}],
)
def test_quality_cost_and_latency_gates(mutation):
    r = reports()
    for row in r["cheap"]:
        row.update(mutation)
    # Nonfinite values are rejected by persistence hashing as well as selection.
    if mutation.get("cost") == float("inf"):
        with pytest.raises(ValueError):
            learn(r)
    else:
        assert learn(r).choose({"source": "x"})[0] == "base"


def test_pairing_and_sparse_segments():
    r = reports()
    r["cheap"].pop()
    with pytest.raises(ValueError):
        learn(r)
    assert learn(reports(), min_cases=50).choose({"source": "x"})[0] == "base"


def test_holdout_blocks_regression_and_never_selects_replacement():
    r = reports()
    assert (
        assess_holdout(r["cheap"], r["base"], min_cases=30)["status"]
        == "eligible_for_manual_adoption"
    )
    r["cheap"][0]["success"] = False
    gate = assess_holdout(r["cheap"], r["base"], min_cases=30)
    assert gate["status"] == "hold" and "quality_gate_failed" in gate["reasons"]
    assert not gate["policy_reselected"]
    zero = [x | {"cost": 0.0} for x in r["base"]]
    assert "no_cost_improvement" in assess_holdout(zero, zero, min_cases=30)["reasons"]
