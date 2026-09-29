"""Evaluation math and baseline safeguards."""

from eval.run_eval import load_questions, regression_reason, score_case, summarize


def test_question_set_has_thirty_public_source_expectations():
    questions = load_questions()
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
