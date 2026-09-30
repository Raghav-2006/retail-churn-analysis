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
