import pandas as pd

from pipeline.quality import CONTRACT, check_raw_schema, diff_contract, dtype_family, volume_anomalies


def test_raw_contract_passes_on_expected_dtypes(raw):
    assert check_raw_schema(raw).status == "PASS"


def test_schema_drift_is_detected(raw):
    drifted = raw.rename(columns={"Price": "UnitPrice"}).astype({"Quantity": "string"})
    check = check_raw_schema(drifted)
    assert check.status == "FAIL"
    assert "missing column 'Price'" in check.detail
    assert "unexpected column 'UnitPrice'" in check.detail
    assert "'Quantity' is string, expected integer" in check.detail


def test_dtype_family_ignores_harmless_differences():
    assert dtype_family(pd.Series(["a"], dtype=object).dtype) == "string"
    assert dtype_family(pd.Series(["a"], dtype="string").dtype) == "string"
    assert dtype_family(pd.Series(pd.to_datetime(["2011-01-01"])).dtype) == "datetime"
    assert dtype_family(pd.Series([1], dtype="int32").dtype) == "integer"


def test_diff_contract_empty_when_equal():
    assert diff_contract(CONTRACT["raw"], CONTRACT["raw"]) == []


def test_volume_anomaly_flags_a_spike_but_not_normal_noise():
    counts = pd.Series([100, 104, 98, 101, 99, 103, 100, 400, 102], dtype=float)
    out = volume_anomalies(counts)
    assert list(out.index[out["anomaly"]]) == [7]


def test_volume_anomaly_flags_a_drop():
    counts = pd.Series([100, 104, 98, 101, 99, 103, 10], dtype=float)
    assert volume_anomalies(counts)["anomaly"].iloc[-1]


def test_partial_period_flags_a_short_final_month_but_not_christmas():
    from pipeline.quality import partial_periods

    cov = pd.DataFrame({
        "month": ["2010-12-01", "2011-11-01", "2011-12-01"],
        "first_day": ["2010-12-01", "2011-11-01", "2011-12-01"],
        "last_day": ["2010-12-23", "2011-11-30", "2011-12-09"],
    })
    out = partial_periods(cov)
    assert list(out["partial"]) == [False, False, True]
    assert out.loc[2, "days_covered"] == 9 and out.loc[2, "coverage"] == round(9 / 31, 3)


def test_partial_period_catches_a_load_that_stopped_mid_month():
    from pipeline.quality import partial_periods

    cov = pd.DataFrame({"month": ["2011-06-01"], "first_day": ["2011-06-01"], "last_day": ["2011-06-10"]})
    assert partial_periods(cov)["partial"].iloc[0]
