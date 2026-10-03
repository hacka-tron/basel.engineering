"""Evaluation math and baseline safeguards."""

import json
from pathlib import Path

import pytest

from eval.run_eval import (
    BASELINE_DIR,
    QUESTIONS_PATH,
    contains_snippet,
    is_noise,
    load_questions,
    question_set_fingerprint,
    regression_reason,
    score_case,
    score_chunks,
    score_retrieval,
    summarize,
)


def test_question_set_has_thirty_public_source_expectations():
    questions = load_questions(QUESTIONS_PATH)
    assert len(questions) == 30
    assert {item["corpus"] for item in questions} == {"about_me", "about_system"}
    assert all(item["expected_sources"] for item in questions)


def test_recall_and_mrr_use_first_relevant_rank():
    assert score_case(["wrong", "expected", "expected"], {"expected"}) == (1, 0.5)
    assert score_case(["wrong"] * 5 + ["expected"], {"expected"}) == (0, 0.0)
    cases = [
        {"corpus": "about_me", "hit": 1, "reciprocal_rank": 0.5},
        {"corpus": "about_system", "hit": 0, "reciprocal_rank": 0.0},
    ]
    assert summarize(cases)["overall"] == {"count": 2, "recall_at_5": 0.5, "mrr": 0.25}


def test_regression_guard_checks_model_content_and_five_point_drop():
    baseline = {
        "embedding_model": "fake-v1",
        "index_name": "idx:chunks",
        "dataset_fingerprint": "a",
        "overall": {"recall_at_5": 0.8},
    }
    result = {
        "embedding_model": "fake-v1",
        "index_name": "idx:chunks",
        "dataset_fingerprint": "a",
        "overall": {"recall_at_5": 0.76},
    }
    assert regression_reason(result, baseline) is None
    result["overall"]["recall_at_5"] = 0.74
    assert "regressed" in regression_reason(result, baseline)
    result["dataset_fingerprint"] = "b"
    assert "Corpus content changed" in regression_reason(result, baseline)


# --- Retrieval eval v2: k=8, chunk-level, noise@8 (synthetic results, no Redis) ---

DOC = "docs/DESIGN.md"
TEST = "services/tests/test_limits.py"
PLAN = "docs/superpowers/plans/2026-09-30-x.md"


def _case(case_id="c", corpus="about_system", category="fact", snippets=("512 MiB",)):
    return {
        "id": case_id,
        "corpus": corpus,
        "category": category,
        "question": "q?",
        "expected_sources": [DOC],
        "gold_snippets": list(snippets),
    }


def _retrieved(*entries):
    return [(index, path, text) for index, (path, text) in enumerate(entries, start=1)]


def test_snippet_match_ignores_case_and_whitespace_runs():
    assert contains_snippet("Memory limit is\n  512   MiB per pod", ["512 mib"])
    assert not contains_snippet("Memory limit is 256 MiB", ["512 MiB"])
    assert contains_snippet("anything", ["nope", "thing"])


def test_chunk_level_uses_first_chunk_with_a_gold_snippet_within_eight():
    chunks = [(DOC, "other"), (DOC, "the DESIGN.md chunk without it"), (DOC, "has 512 MiB here")]
    assert score_chunks(chunks, ["512 MiB"], {DOC}) == (1, pytest.approx(1 / 3))
    assert score_chunks([(DOC, "x")] * 8 + [(DOC, "512 MiB")], ["512 MiB"], {DOC}) == (0, 0.0)


def test_noise_chunk_quoting_the_snippet_is_not_a_chunk_hit():
    # Tests quote doc prose verbatim; only the expected source's chunk counts.
    chunks = [(TEST, "assert '512 MiB' in prompt"), (PLAN, "512 MiB"), (DOC, "limit is 512 MiB")]
    assert score_chunks(chunks, ["512 MiB"], {DOC}) == (1, pytest.approx(1 / 3))
    assert score_chunks(chunks[:2], ["512 MiB"], {DOC}) == (0, 0.0)
    case = score_retrieval(_case(), _retrieved((TEST, "512 MiB quoted"), (DOC, "limit is 512 MiB")))
    assert (case["chunk_hit"], case["chunk_reciprocal_rank"]) == (1, 0.5)
    assert case["noise_count"] == 1


def test_noise_prefixes_cover_tests_and_plans_only():
    assert is_noise(TEST) and is_noise(PLAN)
    assert not is_noise(DOC)
    assert not is_noise("services/glassbox/api/ask.py")
    assert not is_noise("docs/superpowers/README.md")


def test_score_retrieval_file_hit_at_seven_counts_at_eight_not_five():
    retrieved = _retrieved(
        *[(TEST, "test code")] * 3, *[(PLAN, "plan")] * 3, (DOC, "it is 512 MiB"), (DOC, "x")
    )
    case = score_retrieval(_case(), retrieved)
    assert (case["hit"], case["reciprocal_rank"]) == (0, 0.0)
    assert case["hit_at_8"] == 1
    assert case["reciprocal_rank_at_8"] == pytest.approx(1 / 7)
    assert (case["chunk_hit"], case["chunk_reciprocal_rank"]) == (1, pytest.approx(1 / 7))
    assert case["noise_count"] == 6 and case["retrieved_count"] == 8
    assert len(case["retrieved_sources"]) == 5
    assert [item["chunk_id"] for item in case["retrieved_at_8"]] == list(range(1, 9))


def test_right_file_wrong_chunk_is_a_file_hit_but_a_chunk_miss():
    case = score_retrieval(_case(), _retrieved((DOC, "unrelated section of DESIGN.md")))
    assert case["hit"] == 1 and case["hit_at_8"] == 1
    assert (case["chunk_hit"], case["chunk_reciprocal_rank"]) == (0, 0.0)


def test_case_without_gold_snippets_is_left_out_of_chunk_metrics():
    case = score_retrieval(_case(snippets=()), _retrieved((DOC, "512 MiB")))
    assert case["chunk_hit"] is None
    assert summarize([case])["overall"]["chunk_recall_at_8"] is None
    assert summarize([case])["overall"]["chunk_count"] == 0


def test_summary_reports_v2_metrics_per_corpus_and_category():
    cases = [
        score_retrieval(_case("a"), _retrieved((DOC, "512 MiB"), *[(TEST, "t")] * 7)),
        score_retrieval(
            _case("b", category="live"), _retrieved((PLAN, "p"), (DOC, "no"), (DOC, "x"))
        ),
        score_retrieval(
            _case("c", corpus="about_me", snippets=()), _retrieved(("corpus/about-me/bio.md", "b"))
        ),
    ]
    summary = summarize(cases)
    overall = summary["overall"]
    assert overall["count"] == 3
    assert overall["recall_at_5"] == pytest.approx(2 / 3, abs=1e-4)
    assert overall["mrr"] == pytest.approx((1 + 0.5) / 3, abs=1e-4)
    assert overall["recall_at_8"] == overall["recall_at_5"]
    assert overall["chunk_count"] == 2
    assert overall["chunk_recall_at_8"] == 0.5
    assert overall["chunk_mrr_at_8"] == 0.5
    # 7 test chunks + 1 plan chunk out of 8 + 3 + 1 = 12 retrieved.
    assert overall["noise_at_8"] == pytest.approx(8 / 12, abs=1e-4)
    assert set(summary["by_corpus"]) == {"about_me", "about_system"}
    assert summary["by_corpus"]["about_me"]["chunk_recall_at_8"] is None
    assert summary["by_category"]["live"]["noise_at_8"] == pytest.approx(1 / 3, abs=1e-4)
    assert summary["by_category"]["fact"]["count"] == 2


def _v2(**overall):
    base = {"recall_at_5": 0.8, "chunk_recall_at_8": 0.7, "noise_at_8": 0.3}
    return {
        "embedding_model": "fake-v1",
        "index_name": "idx:chunks",
        "dataset_fingerprint": "a",
        "question_set_fingerprint": "q",
        "overall": {**base, **overall},
    }


def test_regression_gates_chunk_recall_strictly_and_noise_with_five_points():
    baseline = _v2()
    assert regression_reason(_v2(chunk_recall_at_8=0.7, noise_at_8=0.34), baseline) is None
    assert regression_reason(_v2(chunk_recall_at_8=0.75), baseline) is None
    # DESIGN-005 §5.4: chunk-level recall@8 must not drop at all.
    assert "Chunk-level recall@8 regressed" in regression_reason(
        _v2(chunk_recall_at_8=0.69), baseline
    )
    assert "Noise@8 rose" in regression_reason(_v2(noise_at_8=0.36), baseline)
    assert "Recall@5 regressed" in regression_reason(_v2(recall_at_5=0.7), baseline)
    assert "Chunk-level" in regression_reason(_v2(chunk_recall_at_8=None), baseline)


def test_regression_refuses_a_different_question_set():
    result = _v2()
    result["question_set_fingerprint"] = "other"
    assert "Question set changed" in regression_reason(result, _v2())


@pytest.mark.parametrize("name", ["fake-v1.json"])
def test_committed_v1_baselines_still_parse_and_gate_on_recall_at_5(name):
    baseline = json.loads((BASELINE_DIR / name).read_text())
    assert "question_set_fingerprint" not in baseline
    result = {
        **_v2(recall_at_5=baseline["overall"]["recall_at_5"]),
        "embedding_model": baseline["embedding_model"],
        "dataset_fingerprint": baseline["dataset_fingerprint"],
        "cases": [{"id": case["id"]} for case in reversed(baseline["cases"])],
    }
    # v1 baselines lack the v2 fields, so recall@5 and the case ids gate.
    assert regression_reason(result, baseline) is None
    result["overall"]["recall_at_5"] -= 0.06
    assert "Recall@5 regressed" in regression_reason(result, baseline)
    result["overall"]["recall_at_5"] += 0.06
    result["cases"].append({"id": "new-golden-case"})
    assert "Question set changed" in regression_reason(result, baseline)


def test_committed_titan_baseline_is_v2_and_gates_on_chunk_recall():
    baseline = json.loads((BASELINE_DIR / "amazon.titan-embed-text-v2_0.json").read_text())
    assert "question_set_fingerprint" in baseline
    assert baseline["overall"]["chunk_recall_at_8"] is not None
    assert baseline["overall"]["noise_at_8"] is not None


def test_loader_accepts_golden_cases_and_skips_non_retrieval_cases(tmp_path: Path):
    cases = [
        {"id": f"c{i}", "corpus": "about_me", "question": "q", "expected_sources": ["a.md"]}
        for i in range(25)
    ]
    cases[0]["gold_snippets"] = ["exact words"]
    cases += [
        {"id": "u", "corpus": "about_me", "question": "salary?", "category": "unanswerable"},
        {
            "id": "m",
            "corpus": "about_me",
            "question": "and then?",
            "expected_sources": ["a.md"],
            "history": [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}],
        },
    ]
    path = tmp_path / "golden.yaml"
    path.write_text(json.dumps({"version": 2, "cases": cases}))
    loaded = load_questions(path)
    assert [item["id"] for item in loaded] == [f"c{i}" for i in range(25)]
    cases[1]["gold_snippets"] = ["  "]
    path.write_text(json.dumps({"cases": cases}))
    with pytest.raises(ValueError, match="empty gold snippet"):
        load_questions(path)


def test_question_set_fingerprint_tracks_scoring_inputs_only():
    questions = load_questions(QUESTIONS_PATH)
    fingerprint = question_set_fingerprint(questions)
    assert question_set_fingerprint(list(reversed(questions))) == fingerprint
    changed = [dict(questions[0], gold_snippets=["new"]), *questions[1:]]
    assert question_set_fingerprint(changed) != fingerprint


def test_default_dataset_loads():
    assert len(load_questions()) >= 25
