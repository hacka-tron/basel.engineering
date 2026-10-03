"""Labelling page generator (XSS-safe, kind-free) and the label importer."""

import json

import pytest
import yaml

from eval import import_labels, label_page
from eval.calibration_candidates import answer_hash

EVIL = '<script>alert(1)</script> & "q" </script>'


def _pool():
    return [
        {
            "id": f"thin:c{i}",
            "case_id": f"c{i}",
            "kind": "thin",
            "question": f"Q{i} {EVIL}",
            "answer": f"Answer {i} {EVIL} 4242 Zorblax",
            "answer_hash": answer_hash(f"Answer {i} {EVIL} 4242 Zorblax"),
            "sources": [{"n": 1, "source_path": f"p/{i}.md <b>", "text": f"src {EVIL}"}],
        }
        for i in range(2)
    ]


def _yaml_text(pool):
    out = "# header comment\nversion: 1\nseed: s\ndev_fraction: 0.4\nitems:\n"
    for item in pool:
        out += (
            f"  - id: {item['id']}\n    case_id: {item['case_id']}\n    kind: thin\n"
            f"    answer_hash: {answer_hash(item['answer'])}  # pragma: allowlist secret\n"
            "    faithful: null   # pass | fail\n    relevant: null   # pass | fail\n"
            '    reason: ""\n    split: dev\n'
        )
    return out


@pytest.fixture
def files(tmp_path):
    pool = _pool()
    pool_path = tmp_path / "pool.jsonl"
    pool_path.write_text("\n".join(json.dumps(p) for p in pool) + "\n")
    yaml_path = tmp_path / "cal.yaml"
    yaml_path.write_text(_yaml_text(pool))
    return pool_path, yaml_path, tmp_path


def test_page_escapes_html_and_hides_kind(files):
    pool_path, yaml_path, tmp = files
    out = tmp / "label.html"
    assert (
        label_page.main(["--pool", str(pool_path), "--yaml", str(yaml_path), "--out", str(out)])
        == 0
    )
    html = out.read_text()
    assert html.count("<script") == 2  # the data block and the app, nothing injected
    assert "<script>alert(1)" not in html and "md <b>" not in html
    data = html.split('id="data">')[1].split("</script>")[0]
    items = json.loads(data)
    assert items[0]["answer"].endswith("4242 Zorblax") and EVIL in items[0]["answer"]
    assert "kind" not in items[0] and items[0]["title"] == "c0"
    assert "el('code',null,item.title)" in html  # header shows the case id, not kind:id
    assert "http://" not in html.replace("http://www.w3.org", "") and "https://" not in html


def _labels(**over):
    base = {
        "thin:c0": {"faithful": "fail", "relevant": "pass", "reason": 'bad: "x" # y'},
        "thin:c1": {"faithful": "pass", "relevant": None, "reason": ""},
    }
    base.update(over)
    return base


def test_import_merges_only_label_fields_and_is_idempotent(files):
    pool_path, yaml_path, tmp = files
    lab = tmp / "labels.json"
    lab.write_text(json.dumps(_labels()))
    before = yaml_path.read_text()
    import_labels.import_labels(lab, yaml_path, pool_path)
    after = yaml_path.read_text()
    assert after.startswith("# header comment") and "pragma: allowlist secret" in after
    items = yaml.safe_load(after)["items"]
    assert items[0]["faithful"] == "fail" and items[0]["relevant"] == "pass"
    assert items[0]["reason"] == 'bad: "x" # y'
    assert items[1]["faithful"] == "pass" and items[1]["relevant"] is None
    stripped = lambda t: [x for x in yaml.safe_load(t)["items"]]  # noqa: E731
    for a, b in zip(stripped(before), items, strict=True):
        for key in ("id", "case_id", "kind", "answer_hash", "split"):
            assert a[key] == b[key]
    import_labels.import_labels(lab, yaml_path, pool_path)
    assert yaml_path.read_text() == after


def test_import_never_erases_existing_labels(files):
    pool_path, yaml_path, tmp = files
    lab = tmp / "labels.json"
    lab.write_text(json.dumps(_labels()))
    import_labels.import_labels(lab, yaml_path, pool_path)
    lab.write_text(json.dumps({"thin:c0": {"faithful": None, "relevant": None, "reason": ""}}))
    import_labels.import_labels(lab, yaml_path, pool_path)
    assert yaml.safe_load(yaml_path.read_text())["items"][0]["faithful"] == "fail"


@pytest.mark.parametrize(
    "labels",
    [
        {"nope:x": {"faithful": "pass"}},
        {"thin:c0": {"faithful": "maybe"}},
        {"thin:c0": {"reason": 5}},
    ],
)
def test_import_rejects_bad_input(files, labels):
    pool_path, yaml_path, tmp = files
    lab = tmp / "labels.json"
    lab.write_text(json.dumps(labels))
    before = yaml_path.read_text()
    with pytest.raises(import_labels.ImportError_):
        import_labels.import_labels(lab, yaml_path, pool_path)
    assert yaml_path.read_text() == before


def test_import_rejects_changed_answer(files):
    pool_path, yaml_path, tmp = files
    pool = _pool()
    pool[0]["answer"] = "different"
    pool_path.write_text("\n".join(json.dumps(p) for p in pool) + "\n")
    lab = tmp / "labels.json"
    lab.write_text(json.dumps(_labels()))
    with pytest.raises(import_labels.ImportError_, match="answer changed"):
        import_labels.import_labels(lab, yaml_path, pool_path)
