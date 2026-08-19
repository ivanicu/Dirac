import json
import random

from scripts.research_loop_multilingual_benchmark import (
    _apply_noise,
    build_context,
    generate_cases,
)


def test_generated_matrix_is_replayable_but_changes_with_the_seed():
    first = generate_cases(1729, 24)
    replay = generate_cases(1729, 24)
    fresh = generate_cases(1730, 24)

    assert first == replay
    assert [case.intent for case in first] != [case.intent for case in fresh]
    assert [case.target for case in first].count("run") == 12
    assert [case.target for case in first].count("stop") == 12
    assert all(4 <= len(case.languages) <= 8 for case in first)
    assert all(len(case.noise_operators) >= 2 for case in first)
    assert sum("quoted_injection_noise" in case.noise_operators for case in first) == 8
    assert sum("discarded_opposite_quote" in case.noise_operators for case in first) == 6
    assert sum("duplicate_fragment" in case.noise_operators for case in first) == 4


def test_every_noise_operator_really_changes_text():
    source = "Continue the planned calcul with scientific références 计算停止."
    for index, operator in enumerate([
        "known_typo", "repeat_character", "drop_character", "zero_width",
        "space_collapse", "case_noise", "unicode_nfd", "punctuation_burst",
    ]):
        assert _apply_noise(source, operator, random.Random(index)) != source


def test_mixed_unicode_goal_survives_the_frozen_context_byte_for_byte():
    case = generate_cases(8675309, 2)[0]
    document, encoded = build_context(case)

    assert document["goal"]["intent"] == case.intent
    assert json.loads(encoded)["goal"]["intent"] == case.intent
    assert document["available_actions"][0]["template_id"] == "fep.run_selected_edge.v1"
    assert document["available_actions"][1]["template_id"] == "fep.stop.v1"
