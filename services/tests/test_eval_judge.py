"""LLM judge, calibration split/agreement and the `--judge` wiring, all with a fake judge."""

import asyncio
import json

import pytest
import yaml

from eval import calibrate, run_answers
from eval import calibration_candidates as cands
from eval import judge as judge_mod
from eval.judge import DEFAULT_JUDGE_MODEL_ID, Judge, parse_verdict
from services.glassbox.api.ask import WorkerChunk
from services.glassbox.providers.base import LLMProvider
from services.glassbox.providers.fake import FakeEmbeddingProvider, FakeLLMProvider


class ScriptedJudgeLLM(LLMProvider):
    """Returns replies from a function of (system, prompt); records the calls."""

    model_id = "scripted-judge"

    def __init__(self, reply):
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    async def generate(self, prompt, *, max_tokens, system=None):
        self.calls.append((system, prompt))
        yield self.reply(system, prompt)


def run(coro):
    return asyncio.run(coro)


SOURCES = [{"n": 1, "source_path": "docs/a.md", "text": "KEDA scales to 3 pods."}]


# ------------------------------------------------------------------ parsing


def test_parse_valid_fenced_and_wrapped_json():
    assert parse_verdict('{"pass": true, "critique": "ok"}').passed is True
    assert parse_verdict('```json\n{"pass": false, "critique": "bad"}\n```').passed is False
    wrapped = parse_verdict('Here you go: {"pass": false, "critique": "x"} thanks')
    assert (wrapped.passed, wrapped.critique) == (False, "x")


@pytest.mark.parametrize(
    "text",
    [
        "",
        "no json here",
        '{"pass": "yes"}',
        '{"pass": 1}',
        '{"critique": "x"}',
        "{broken",
        "[true]",
    ],
)
def test_parse_malformed_is_an_error_not_a_pass(text):
    verdict = parse_verdict(text)
    assert verdict.passed is None and verdict.error


def test_judge_sees_question_numbered_sources_answer_and_no_expected_answer():
    llm = ScriptedJudgeLLM(lambda s, p: '{"pass": true, "critique": "fine"}')
    out = run(Judge(llm).evaluate("How many pods?", SOURCES, "Three pods."))
    assert set(out) == {"faithfulness", "relevance"} and all(v.passed for v in out.values())
    systems = {system for system, _ in llm.calls}
    assert systems == set(judge_mod.RUBRICS.values())
    prompt = llm.calls[0][1]
    assert "[1] docs/a.md: KEDA scales to 3 pods." in prompt
    assert "Question: How many pods?" in prompt and "Three pods." in prompt
    assert "must_include" not in prompt


def test_judge_marks_planned_text_like_the_generator_and_survives_errors():
    planned = [{"n": 1, "source_path": "docs/a.md", "text": "- Hybrid search is planned."}]
    assert "[PLANNED, not built yet]" in judge_mod.format_sources(planned)

    class Boom(ScriptedJudgeLLM):
        async def generate(self, prompt, *, max_tokens, system=None):
            raise RuntimeError("throttled")
            yield ""

    out = run(Judge(Boom(None)).evaluate("q", SOURCES, "a"))
    assert all(v.passed is None and "throttled" in v.error for v in out.values())


def test_default_judge_model_is_nova_pro_and_overridable(monkeypatch):
    monkeypatch.delenv("GLASSBOX_JUDGE_MODEL_ID", raising=False)
    assert judge_mod.judge_model_id() == DEFAULT_JUDGE_MODEL_ID == "us.amazon.nova-pro-v1:0"
    monkeypatch.setenv("GLASSBOX_JUDGE_MODEL_ID", "other")
    assert judge_mod.judge_model_id() == "other"


def test_paid_judge_needs_the_allow_flag(monkeypatch):
    monkeypatch.setenv("GLASSBOX_PROVIDER", "bedrock")
    monkeypatch.delenv("GLASSBOX_EVAL_ALLOW_PAID", raising=False)
    with pytest.raises(judge_mod.JudgePaidRefused):
        judge_mod.get_judge_llm()
    monkeypatch.setenv("GLASSBOX_PROVIDER", "fake")
    assert judge_mod.get_judge_llm().model_id == "fake-judge-v1"


# ------------------------------------------------------------------ split and agreement


def test_split_is_deterministic_and_each_item_has_exactly_one_split():
    ids = [f"thin:case-{i}" for i in range(400)]
    first = [cands.assign_split(i) for i in ids]
    assert first == [cands.assign_split(i) for i in ids]
    assert set(first) == {"dev", "test"}
    assert 0.3 < first.count("dev") / len(ids) < 0.5
    assert cands.assign_split("x", seed="a") in {"dev", "test"}
    assert [cands.assign_split(i, seed="other") for i in ids] != first


def test_agreement_math_counts_unusable_verdicts_as_disagreement():
    pairs = (
        [(True, True)] * 8 + [(True, False), (True, None)] + [(False, False)] * 3 + [(False, True)]
    )
    result = calibrate.agreement(pairs)
    assert result == {"n_pass": 10, "n_fail": 4, "tpr": 0.8, "tnr": 0.75}


def test_calibrate_refuses_to_report_under_ten_per_class():
    few = [(True, True)] * 9 + [(False, False)] * 12
    out = calibrate.evaluate_agreement(few)
    assert out["reportable"] is False and out["tpr"] is None and out["tnr"] is None
    enough = [(True, True)] * 10 + [(False, False)] * 10
    out = calibrate.evaluate_agreement(enough)
    assert out["reportable"] and out["meets_target"] is True
    low = [(True, True)] * 8 + [(True, False)] * 2 + [(False, False)] * 10
    assert calibrate.evaluate_agreement(low)["meets_target"] is False


# ------------------------------------------------------------------ calibration files


def _item(kind, case, answer="An answer."):
    return cands.make_item(
        kind,
        {"id": case, "category": "fact", "question": f"Q {case}?"},
        answer=answer,
        sources=SOURCES,
    )


def _write(tmp_path, items, entries):
    cal = tmp_path / "calibration.yaml"
    cal.write_text((cands.CALIBRATION_PATH).read_text())
    pool = tmp_path / "pool.jsonl"
    cands.write_pool(items, pool)
    assert cands.append_yaml_items(items, cal) == len(items)
    data = yaml.safe_load(cal.read_text())
    for entry in data["items"]:
        entry.update(entries.get(entry["id"], {}))
    cal.write_text(yaml.safe_dump(data))
    return cal, pool


def test_template_is_empty_and_append_keeps_existing_entries(tmp_path):
    assert yaml.safe_load(cands.CALIBRATION_PATH.read_text())["items"] == []
    items = [_item("thin", "a"), _item("perturbed", "b")]
    cal = tmp_path / "calibration.yaml"
    cal.write_text(cands.CALIBRATION_PATH.read_text())
    assert cands.append_yaml_items(items[:1], cal) == 1
    cal.write_text(cal.read_text().replace("faithful: null", "faithful: pass"))
    assert cands.append_yaml_items(items, cal) == 1  # only the new one
    data = yaml.safe_load(cal.read_text())
    assert [e["id"] for e in data["items"]] == ["thin:a", "perturbed:b"]
    assert data["items"][0]["faithful"] == "pass" and data["items"][1]["faithful"] is None
    assert cands.append_yaml_items(items, cal) == 0


def test_load_labelled_validates_split_hash_and_labels(tmp_path):
    items = [_item("thin", "a"), _item("thin", "b")]
    cal, pool = _write(
        tmp_path, items, {"thin:a": {"faithful": "pass", "relevant": "fail", "reason": "x"}}
    )
    loaded = calibrate.load_labelled(cal, pool)
    assert [i["id"] for i in loaded] == ["thin:a"]  # unlabelled items are skipped
    assert loaded[0]["labels"] == {"faithfulness": True, "relevance": False}

    data = yaml.safe_load(cal.read_text())
    data["items"][0]["split"] = "dev" if data["items"][0]["split"] == "test" else "test"
    cal.write_text(yaml.safe_dump(data))
    with pytest.raises(calibrate.CalibrationError, match="split"):
        calibrate.load_labelled(cal, pool)

    cal, pool = _write(tmp_path, items, {"thin:a": {"faithful": "pass"}})
    cands.write_pool([_item("thin", "a", "A different answer."), items[1]], pool)
    with pytest.raises(calibrate.CalibrationError, match="answer changed"):
        calibrate.load_labelled(cal, pool)

    cal, pool = _write(tmp_path, items, {"thin:a": {"faithful": "maybe"}})
    with pytest.raises(calibrate.CalibrationError, match="pass, fail or null"):
        calibrate.load_labelled(cal, pool)


def test_dev_disagreements_are_printed_but_test_texts_never_are(tmp_path):
    items = [_item("thin", f"c{i}", f"Answer number {i}.") for i in range(40)]
    entries = {
        item["id"]: {"faithful": "pass", "relevant": "pass", "reason": f"reason {item['id']}"}
        for item in items
    }
    cal, pool = _write(tmp_path, items, entries)
    labelled = calibrate.load_labelled(cal, pool)
    judge = Judge(ScriptedJudgeLLM(lambda s, p: '{"pass": false, "critique": "nope"}'))
    judged = run(calibrate.judge_items(judge, labelled))
    result = calibrate.report(judged, splits=("dev", "test"))
    dev_ids = {i["id"] for i in labelled if i["split"] == "dev"}
    test_ids = {i["id"] for i in labelled if i["split"] == "test"}
    assert dev_ids and test_ids and not dev_ids & test_ids
    shown = result["faithfulness"]["dev_disagreements"]
    assert {d["id"] for d in shown} == dev_ids
    assert shown[0]["judge_critique"] == "nope" and shown[0]["owner"] == "pass"
    text = json.dumps(result)
    assert not any(f"Answer number {i}." in text for i in range(40) if f"thin:c{i}" in test_ids)
    assert not any(f'"{t}"' in text for t in test_ids)
    # all labels are pass and the judge says fail: tpr 0, but the test split has no fail class
    assert result["faithfulness"]["test"]["reportable"] is False


def test_test_reuse_with_a_changed_prompt_warns(tmp_path, monkeypatch):
    log = tmp_path / "log.jsonl"
    assert calibrate.note_test_use("m", log) is None
    assert calibrate.note_test_use("m", log) is None
    assert "fresh test labels" in calibrate.note_test_use("m2", log)


# ------------------------------------------------------------------ candidates


def test_perturbations_change_the_text():
    assert (
        cands.perturb_numbers("5-minute cooldown, 512 MiB, 1.5x")
        == "13-minute cooldown, 1027 MiB, 6.0x"
    )
    assert "live" in cands.perturb_status("Hybrid search is planned.")
    assert cands.perturb_sources([{"n": 1, "source_path": "x", "text": "nothing here"}])[1] is None
    changed, how = cands.perturb_sources([{"n": 1, "source_path": "x", "text": "3 pods"}])
    assert how == "number" and changed[0]["text"] == "9 pods"


def test_without_abstention_rewrites_the_real_prompt():
    from services.glassbox.api.ask import _prompt

    chunk = WorkerChunk(n=1, chunk_id=1, text="t", source_path="docs/a.md", title="t", score=0.5)
    prompt = cands.without_abstention(_prompt("q?", [chunk]))
    assert "reply with exactly" not in prompt and "best answer anyway" in prompt


def _row(case, answer, coverage, corpus="about_system", category="fact"):
    return {
        "id": case,
        "category": category,
        "corpus": corpus,
        "question": f"Q {case}?",
        "answer": answer,
        "sources": SOURCES,
        "error": None,
        "grades": {"fact_coverage": coverage},
    }


def test_thin_and_mismatched_items_from_a_run():
    rows = [_row("a", "thin", 0.5), _row("b", "full", 1.0), _row("c", "other", 1.0)]
    rows.append({**_row("d", "x", 0.0), "sources": []})
    assert [i["id"] for i in cands.thin_items(rows)] == ["thin:a"]
    mism = cands.mismatched_items(rows)
    assert {i["case_id"] for i in mism} == {"a", "b", "c"}
    assert all(i["answer_from"] != i["case_id"] for i in mism)
    assert cands.mismatched_items(rows) == mism  # deterministic
    assert cands.pool_counts(cands.thin_items(rows) + mism)["mismatched"] == 3


def test_merge_pool_never_replaces_an_existing_labelled_answer():
    old = [_item("thin", "a", "old")]
    merged = cands.merge_pool(old, [_item("thin", "a", "new"), _item("thin", "b")])
    assert [i["answer"] for i in merged] == ["old", "An answer."]


def test_sheet_contains_text_but_yaml_block_does_not():
    item = _item("thin", "a", "Secret answer text.")
    assert "Secret answer text." in cands.render_sheet([item])
    assert "Secret" not in cands.yaml_block(item, seed=cands.DEFAULT_SEED, dev_fraction=0.4)


# ------------------------------------------------------------------ run_answers wiring


def _stub_retriever(calls):
    async def retrieve(vector, corpus, model_id):
        calls.append(corpus)
        return [
            WorkerChunk(
                n=1,
                chunk_id=7,
                text="KEDA scales to 3 pods.",
                source_path="docs/architecture/deep-dive.md",
                title="t",
                score=0.8,
            )
        ]

    return retrieve


def test_judge_flag_adds_verdicts_to_rows_and_summary():
    cases = [
        {"id": "x1", "corpus": "about_system", "category": "fact", "question": "How many pods?"},
        {
            "id": "x2",
            "corpus": "about_system",
            "category": "unanswerable",
            "question": "Salary?",
            "expect_abstain": True,
        },
    ]

    def reply(system, prompt):
        return json.dumps({"pass": "relevance" not in system.lower()[:400], "critique": "c"})

    judge = Judge(ScriptedJudgeLLM(reply))
    rows = run(
        run_answers.run_cases(
            cases,
            embedder=FakeEmbeddingProvider(),
            llm=FakeLLMProvider(),
            retrieve=_stub_retriever([]),
            judge=judge,
        )
    )
    for row in rows:
        assert row["sources"][0]["text"] == "KEDA scales to 3 pods."
        assert set(row["judge"]["verdicts"]) == {"faithfulness", "relevance"}
    overall = run_answers.summarize(rows)["overall"]
    assert overall["judge_count"] == 2 and overall["judge_errors"] == 0
    assert overall["judge_faithfulness_rate"] == 1.0
    assert overall["judge_relevance_rate"] is not None
    # unanswerable rows are excluded from the relevance rate
    only_unans = run_answers.summarize(rows[1:])["overall"]
    assert only_unans["judge_relevance_rate"] is None


def test_runs_without_judge_have_no_judge_keys():
    cases = [{"id": "x1", "corpus": "about_system", "category": "fact", "question": "q?"}]
    rows = run(
        run_answers.run_cases(
            cases,
            embedder=FakeEmbeddingProvider(),
            llm=FakeLLMProvider(),
            retrieve=_stub_retriever([]),
        )
    )
    assert "judge" not in rows[0]
    assert "judge_count" not in run_answers.summarize(rows)["overall"]


# ------------------------------------------------------------------ review round 1


def test_multi_turn_judge_gets_the_standalone_rewrite_not_the_bare_follow_up():
    cases = [
        {
            "id": "mt",
            "corpus": "about_system",
            "category": "multi_turn",
            "question": "And the second one?",
            "history": [
                {"role": "user", "content": "Name two autoscalers."},
                {"role": "assistant", "content": "KEDA and HPA."},
            ],
        }
    ]
    llm = ScriptedJudgeLLM(lambda s, p: '{"pass": true, "critique": "c"}')
    rows = run(
        run_answers.run_cases(
            cases,
            embedder=FakeEmbeddingProvider(),
            llm=ScriptedJudgeLLM(lambda s, p: "What is the second autoscaler?"),
            retrieve=_stub_retriever([]),
            judge=Judge(llm),
        )
    )
    row = rows[0]
    asked = row["judge"]["question"]
    assert asked == row["rewrite"] == "What is the second autoscaler?"
    assert all(f"Question: {asked}" in prompt for _, prompt in llm.calls)
    # Pool items carry the standalone question too.
    row = {
        **_row("mt", "ans", 0.5, category="multi_turn"),
        "rewrite": "What is the second autoscaler?",
    }
    assert cands.thin_items([{**row, "category": "fact"}])[0]["question"] == row["rewrite"]


def _verdict_row(name_pass):
    v = lambda p: {"pass": p, "critique": "", "tokens_in": 1, "tokens_out": 1}  # noqa: E731
    return {
        "category": "fact",
        "grades": {},
        "judge": {"verdicts": {"faithfulness": v(name_pass), "relevance": v(True)}},
    }


def test_judge_errors_invalidate_the_rates():
    answerable = [_verdict_row(True), _verdict_row(True)]
    clean = run_answers.judge_metrics(answerable, answerable)
    assert clean["judge_errors"] == 0 and clean["judge_faithfulness_rate"] == 1.0
    broken = [_verdict_row(True), _verdict_row(None)]
    bad = run_answers.judge_metrics(broken, broken)
    assert bad["judge_errors"] == 1
    assert bad["judge_faithfulness_rate"] is None and bad["judge_relevance_rate"] is None
    assert bad["judge_faithfulness_rate_partial"] == 1.0


def test_mismatched_answers_never_come_from_abstentions():
    rows = [
        _row("a", "real answer a", 1.0),
        _row("b", "real answer b", 1.0),
        _row("u1", "I do not know.", 1.0, category="unanswerable"),
        _row("u2", "I do not know.", 1.0, category="unanswerable"),
    ]
    items = cands.mismatched_items(rows)
    assert {i["case_id"] for i in items} == {"a", "b"}
    assert all(i["answer_from"] in {"a", "b"} for i in items)


def test_calibrate_reports_agreement_per_kind():
    class V:
        def __init__(self, passed):
            self.passed, self.critique, self.error = passed, "", None

    def item(i, kind, owner, judge):
        return {
            "id": i, "kind": kind, "split": "test", "reason": "", "question": "q", "answer": "a",
            "labels": {"faithfulness": owner, "relevance": None},
            "verdicts": {"faithfulness": V(judge), "relevance": V(True)},
        }  # fmt: skip

    judged = [item("m1", "mismatched", False, False), item("t1", "thin", False, True)]
    by_kind = calibrate.report(judged, splits=("test",))["faithfulness"]["test_by_kind"]
    assert by_kind["mismatched"]["tnr"] == 1.0 and by_kind["thin"]["tnr"] == 0.0


def test_sheet_marks_splits_and_warns_about_test_items():
    items = [_item("thin", f"c{i}") for i in range(20)]
    sheet = cands.render_sheet(items)
    assert "WARNING" in sheet.splitlines()[2] or "WARNING" in sheet[:400]
    for it in items:
        split = cands.assign_split(it["id"]).upper()
        assert f"## {it['id']} [{split}]" in sheet
    assert "[DEV]" in sheet and "[TEST]" in sheet
