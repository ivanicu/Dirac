import json
import random
from pathlib import Path

from scripts.research_loop_multilingual_benchmark import (
    _apply_noise,
    build_context,
    generate_cases,
)
from research.reasoner import (
    _goal_action_choices,
    _narrow_acquisition_choices,
    build_goal_interpretation_messages,
)


def test_generated_matrix_is_replayable_but_changes_with_the_seed():
    first = generate_cases(1729, 32)
    replay = generate_cases(1729, 32)
    fresh = generate_cases(1730, 32)

    assert first == replay
    assert [case.intent for case in first] != [case.intent for case in fresh]
    assert {case.target for case in first} == {
        "run", "prepare", "replan", "defer", "stop"}
    assert max([case.target for case in first].count(target)
               for target in {case.target for case in first}) - min(
                   [case.target for case in first].count(target)
                   for target in {case.target for case in first}) <= 1
    assert {case.scenario_family for case in first} == {
        "explicit_pair", "priority_inference", "latest_revision",
        "spoken_self_correction", "nested_quotation_attack",
        "bidi_ocr_transcript", "budget_and_result_history",
        "implicit_scientific_request",
    }
    assert all(8 <= len(case.languages) <= 10 for case in first)
    assert all(72 <= case.fragment_count <= 123 for case in first)
    assert all(len(case.noise_operators) >= 43 for case in first)
    assert all(12 <= case.decoy_count <= 24 for case in first)
    assert all(2 <= case.nesting_depth <= 7 for case in first)
    assert all(len(case.intent) >= 4_000 for case in first)
    assert all(len(case.intent) <= 16_384 for case in first)
    assert all("duplicate_fragment" in case.noise_operators for case in first)
    assert all(8 <= len(case.compounds) <= 20 for case in first)
    assert all(12 <= len(case.edges) <= 30 for case in first
               if case.target != "replan")
    assert all(not case.edges for case in first if case.target == "replan")
    assert all(case.expected_subject_id not in case.intent for case in first
               if case.target in {"run", "prepare"})


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
    templates = {row["template_id"] for row in document["available_actions"]}
    assert case.expected_template in templates
    assert "fep.stop.v1" in templates
    assert "fep.defer_for_experiment.v1" in templates


def test_ground_truth_is_an_exact_operation_not_a_binary_label():
    cases = generate_cases(424242, 1000)
    operations = {(case.expected_template, case.expected_subject_kind,
                   case.expected_command) for case in cases}
    assert {template for template, _kind, _command in operations} == {
        "fep.run_selected_edge.v1", "fep.prepare_selected_edge.v1",
        "fep.replan_network.v1", "fep.defer_for_experiment.v1", "fep.stop.v1",
    }
    edge_subjects = {case.expected_subject_id for case in cases
                     if case.expected_subject_kind == "free_energy_transformation"}
    assert len(edge_subjects) == 400
    assert all(case.expected_operation["subject_ref"] == case.expected_subject
               for case in cases)


def test_authoritative_entity_binding_narrows_every_acquisition_to_ground_truth():
    for case in generate_cases(424242, 1000):
        if case.target not in {"run", "prepare"}:
            continue
        context, _raw = build_context(case)
        _system, user = build_goal_interpretation_messages(
            context, system_prompt="Return JSON only.")
        request = json.loads(user.split("\n", 1)[1])
        choices = [item for item in _goal_action_choices(context)
                   if item["template_id"] in {
                       "fep.run_selected_edge.v1",
                       "fep.prepare_selected_edge.v1"}]
        narrowed = _narrow_acquisition_choices(
            context, choices, request["goal_intent"])
        assert [(item["template_id"], item["subject_ref"]) for item in narrowed] == [
            (case.expected_template, case.expected_subject)]
