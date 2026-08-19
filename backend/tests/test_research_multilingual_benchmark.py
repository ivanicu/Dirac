import json
import random
from pathlib import Path

from scripts.research_loop_multilingual_benchmark import (
    _apply_noise,
    build_context,
    generate_cases,
)


def test_generated_matrix_is_replayable_but_changes_with_the_seed():
    first = generate_cases(1729, 32)
    replay = generate_cases(1729, 32)
    fresh = generate_cases(1730, 32)

    assert first == replay
    assert [case.intent for case in first] != [case.intent for case in fresh]
    assert [case.target for case in first].count("run") == 16
    assert [case.target for case in first].count("stop") == 16
    assert {case.scenario_family for case in first} == {
        "normal_noisy_request", "final_block_last", "final_block_middle",
        "spoken_self_correction", "versioned_ticket_thread",
        "nested_quotation_attack", "bidi_ocr_transcript",
        "implicit_scientific_request",
    }
    assert all(8 <= len(case.languages) <= 10 for case in first)
    assert all(72 <= case.fragment_count <= 115 for case in first)
    assert all(len(case.noise_operators) >= 36 for case in first)
    assert all(12 <= case.decoy_count <= 20 for case in first)
    assert all(2 <= case.nesting_depth <= 6 for case in first)
    assert all(len(case.intent) >= 4_000 for case in first)
    assert all(len(case.intent) <= 16_384 for case in first)
    assert all("duplicate_fragment" in case.noise_operators for case in first)


def test_goal_contracts_accept_ten_x_inputs_without_widening_other_text():
    root = Path(__file__).resolve().parents[2]
    registry = json.loads(
        (root / "contracts/commands/registry.json").read_text(encoding="utf-8"))
    commands = {item["id"]: item for item in registry["commands"]}
    assert commands["research.loop.create"]["input_schema"]["properties"][
        "intent"]["maxLength"] == 16_384
    assert commands["research.loop.control"]["input_schema"]["properties"][
        "revised_intent"]["maxLength"] == 16_384

    context_schema = json.loads((
        root / "contracts/domain/research/context-snapshot.schema.json"
    ).read_text(encoding="utf-8"))
    assert context_schema["properties"]["goal"]["properties"]["intent"] == {
        "$ref": "#/$defs/text16384"
    }
    assert context_schema["$defs"]["text4096"]["maxLength"] == 4_096


def test_every_noise_operator_really_changes_text():
    source = "Continue the planned calcul with scientific références 计算停止."
    for index, operator in enumerate([
        "known_typo", "repeat_character", "drop_character", "zero_width",
        "space_collapse", "case_noise", "unicode_nfd", "punctuation_burst",
        "keyboard_neighbor", "word_transposition", "token_duplication",
        "ocr_confusion", "homoglyph", "bidi_marks", "stray_markup",
        "dictation_filler", "newline_fragmentation",
    ]):
        assert _apply_noise(source, operator, random.Random(index)) != source


def test_mixed_unicode_goal_survives_the_frozen_context_byte_for_byte():
    case = generate_cases(8675309, 16)[0]
    document, encoded = build_context(case)

    assert document["goal"]["intent"] == case.intent
    assert json.loads(encoded)["goal"]["intent"] == case.intent
    assert document["available_actions"][0]["template_id"] == "fep.run_selected_edge.v1"
    assert document["available_actions"][1]["template_id"] == "fep.stop.v1"
