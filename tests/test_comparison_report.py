import json
from pathlib import Path

import pytest

from harness_tuner.compare import build_comparison, comparison_html, main, write_report
from harness_tuner.protocol import write_json
from harness_tuner.verify import VerifyError
from test_verify import build_run


def test_portable_comparison_uses_paired_evidence_and_provenance(tmp_path):
    before = build_run(tmp_path, "before", [(f"t{i}", ["a", "a"], .03) for i in range(12)])
    after = build_run(tmp_path, "after", [(f"t{i}", ["a", "b"], .01) for i in range(12)])
    doc = build_comparison(before.root, after.root)
    cost = next(r for r in doc["rows"] if r["metric"] == "cost_usd")
    assert cost["delta"] == -.02
    assert cost["verdict"] == "improvement"
    assert cost["coverage"] == "12/12"
    assert len(doc["baseline"]["artifact_sha256"]["metrics"]) == 64
    assert str(tmp_path) not in json.dumps(doc)
    write_report(doc, str(tmp_path/"share"))
    assert {p.name for p in (tmp_path/"share").iterdir()} == {"comparison.html","comparison.json","comparison.csv"}
    assert json.loads((tmp_path/"share/comparison.json").read_text()) == doc
    with pytest.raises(FileExistsError):
        write_report(doc, str(tmp_path/"share"))


def test_duplicate_ids_and_protocol_mismatch_are_refused(tmp_path):
    before = build_run(tmp_path, "before", [("a", ["x"], .01)])
    after = build_run(tmp_path, "after", [("a", ["x"], .01)])
    path = Path(after.path("metrics"))
    metrics = json.loads(path.read_text())
    metrics["per_task"].append(metrics["per_task"][0])
    write_json(str(path), metrics)
    with pytest.raises(VerifyError, match="unique"):
        build_comparison(before.root, after.root)
    metrics["per_task"].pop()
    metrics["htp_version"] = "different"
    write_json(str(path), metrics)
    with pytest.raises(VerifyError, match="protocol"):
        build_comparison(before.root, after.root)


def test_unit_mismatch_missing_values_and_zero_baselines_are_visible(tmp_path):
    before = build_run(tmp_path, "before", [("a", ["x"], 0)])
    after = build_run(tmp_path, "after", [("a", ["x"], .01)])
    doc = build_comparison(before.root, after.root)
    assert next(r for r in doc["rows"] if r["metric"]=="cost_usd")["percent_change"] is None
    path = Path(after.path("metrics"))
    metrics = json.loads(path.read_text())
    metrics["per_task"][0]["metrics"]["cost_usd"]["unit"] = "EUR"
    write_json(str(path),metrics)
    row = next(r for r in build_comparison(before.root,after.root)["rows"] if r["metric"]=="cost_usd")
    assert row["tasks_comparable"] == 0
    assert row["unit_mismatches"] == ["a"]
    assert row["mean_variant"] is None


def test_untrusted_task_ids_cannot_escape_embedded_data_or_html(tmp_path):
    task_id = "unsafe</script><script>alert(1)</script>"
    before=build_run(tmp_path,"before",[(task_id,["x"],.01)])
    after=build_run(tmp_path,"after",[(task_id,["x"],.01)])
    document=comparison_html(build_comparison(before.root,after.root))
    assert task_id not in document
    assert "\\u003c/script>" in document
    assert "metric-search" in document
    for invalid in [0,1,float("nan")]:
        with pytest.raises(VerifyError,match="alpha"):
            build_comparison(before.root,after.root,alpha=invalid)


def test_success_pairs_use_task_units_not_aggregate_ratios(tmp_path):
    before = build_run(tmp_path, "before", [(f"t{i}", ["a"], .03) for i in range(12)])
    after = build_run(tmp_path, "after", [(f"t{i}", ["a"], .01) for i in range(12)])
    assert json.loads(Path(before.path("metrics")).read_text())["aggregate"]["task_success"]["unit"] == "ratio"
    row = next(r for r in build_comparison(before.root, after.root)["rows"] if r["metric"] == "task_success")
    assert row["coverage"] == "12/12"
    assert row["unit_mismatches"] == []
    assert row["mean_baseline"] == row["mean_variant"] == 1
