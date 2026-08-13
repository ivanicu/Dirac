from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest

from contracts.validation import validator_for
from motif.action_planner import PlannerPolicy, plan_actions
from motif.model_gates import (LifecycleThreshold, ReleaseLifecycle,
                               ValidationPolicy, assess_release)
from motif.resource_broker import AtomicResourceBroker, InsufficientCapacity
from motif.semantics import (
    ApplicabilityState, ClaimEligibility, DecisionDisposition, EvidenceItem,
    EvidenceKind, ExecutionState, MethodOutcome, OrthogonalState, ScientificIdentity,
    ScientificState, aggregate_state_values, assemble_evidence_snapshot,
    require_execution_transition,
)


ROOT = Path(__file__).resolve().parents[2]
NOW = "2026-08-13T12:00:00Z"
UUID = "00000000-0000-4000-8000-000000000001"


def ref(kind: str, identifier: str) -> dict[str, str]:
    return {"kind": kind, "id": identifier}


class OrthogonalSemanticsTests(unittest.TestCase):
    def test_execution_failure_cannot_be_scientific_rejection(self):
        with self.assertRaisesRegex(ValueError, "cannot itself reject"):
            OrthogonalState(ExecutionState.FAILED, scientific=ScientificState.REJECTED)

    def test_refusal_requires_typed_reason(self):
        with self.assertRaisesRegex(ValueError, "reason_codes"):
            OrthogonalState(ExecutionState.SUCCEEDED,
                            disposition=DecisionDisposition.REFUSED)

    def test_retry_cannot_reopen_terminal_attempt(self):
        with self.assertRaisesRegex(ValueError, "new Attempt"):
            require_execution_transition("failed", "queued")
        require_execution_transition("created", "queued")

    def test_scientific_identity_changes_for_environment_and_numeric_contract(self):
        base = ScientificIdentity("pose_hypothesis", ("sha256:" + "a" * 64,),
                                  "sha256:" + "b" * 64, "sha256:" + "c" * 64,
                                  "sha256:" + "d" * 64, "sha256:" + "e" * 64,
                                  "sha256:" + "f" * 64)
        changed = replace(base, environment_digest="sha256:" + "0" * 64)
        numeric = replace(base, numeric_contract_digest="sha256:" + "1" * 64)
        self.assertEqual(len({base.digest(), changed.digest(), numeric.digest()}), 3)

    def test_failed_outcome_cannot_publish_scientific_artifact(self):
        with self.assertRaisesRegex(ValueError, "cannot publish"):
            MethodOutcome("o1", ref("method_run", "m1"), ExecutionState.FAILED,
                          artifact_refs=(ref("artifact", "a1"),))

    def test_operational_failure_probability_is_not_evidence(self):
        with self.assertRaisesRegex(ValueError, "operational failure risk"):
            EvidenceItem(
                "e1", EvidenceKind.SCALAR_ESTIMATE, ref("compound", "c1"),
                ref("condition", "pH7"), ref("method_release", "mr1"),
                ref("method_outcome", "o1"), {"value": 1, "failure_probability": .2},
                ApplicabilityState.APPLICABLE, ScientificState.PROVISIONAL,
                ClaimEligibility.INELIGIBLE_PROVISIONAL_QUALITY,
            )

    def test_evidence_snapshot_excludes_stale_condition_and_dependency(self):
        def item(identifier: str, **kw):
            return EvidenceItem(
                identifier, EvidenceKind.SCALAR_ESTIMATE, ref("compound", "c1"),
                kw.pop("condition_ref", ref("condition", "pH7")),
                ref("method_release", "mr1"), ref("method_outcome", "o1"),
                {"value": 1.0, "unit": "dimensionless"}, ApplicabilityState.APPLICABLE,
                ScientificState.PROVISIONAL,
                kw.pop("claim_eligibility", ClaimEligibility.INELIGIBLE_PROVISIONAL_QUALITY),
                **kw,
            )
        snapshot = assemble_evidence_snapshot([
            item("good", dependency_refs=(ref("artifact", "valid"),),
                 shared_assumption_refs=(ref("assumption", "charge"),)),
            item("stale", stale=True,
                 claim_eligibility=ClaimEligibility.INELIGIBLE_STALE),
            item("wrong-condition", condition_ref=ref("condition", "pH5")),
            item("bad-dependency", dependency_refs=(ref("artifact", "missing"),)),
        ], required_condition_ref=ref("condition", "pH7"),
           valid_dependency_ids={"valid"})
        self.assertEqual(snapshot["evidence_ids"], ["good"])
        self.assertEqual({row["reason_code"] for row in snapshot["excluded"]},
                         {"EVIDENCE_STALE", "CONDITION_INCOMPATIBLE", "DEPENDENCY_INVALID"})

    def test_complex_evidence_requires_its_native_scientific_structure(self):
        with self.assertRaisesRegex(ValueError, "edge_covariance_ref"):
            EvidenceItem(
                "network-1", EvidenceKind.NETWORK_ESTIMATE, ref("network", "n1"),
                ref("condition", "c1"), ref("method_release", "mr1"),
                ref("method_outcome", "o1"),
                {"artifact_ref": ref("artifact", "a1"), "network_ref": ref("network", "n1")},
                ApplicabilityState.APPLICABLE, ScientificState.PROVISIONAL,
                ClaimEligibility.INELIGIBLE_PROVISIONAL_QUALITY,
            )

    def test_parent_aggregation_forbids_best_state_lottery_and_tracks_mass(self):
        result = aggregate_state_values([
            {"state_ref": ref("chemical_microstate", "s1"), "condition": {"pH": 7.4},
             "population": .6, "value": 10.0},
            {"state_ref": ref("chemical_microstate", "s2"), "condition": {"pH": 7.4},
             "population": .3, "value": 4.0},
        ])
        self.assertAlmostEqual(result["estimate"], 8.0)
        self.assertAlmostEqual(result["discarded_population_mass"], .1)
        self.assertTrue(result["lottery_prevented"])
        with self.assertRaisesRegex(ValueError, "unsupported"):
            aggregate_state_values([{"state_ref": ref("chemical_microstate", "s"),
                                     "condition": {}, "population": 1, "value": 1}],
                                   policy="best_state")


class ScientificActionPlannerTests(unittest.TestCase):
    def setUp(self):
        self.policy = PlannerPolicy(
            "policy-1", "utility-1", "outcomes-1", "costs-1",
            {"gpu_hours": 0.2, "walltime_seconds": .0001},
            max_iterations=4, max_actions_per_subject_question=2)

    def action(self, kind: str, subject: str, gain: float, gpu: float) -> dict:
        return {
            "action_kind": kind, "subject_ref": ref("compound", subject),
            "scientific_question": "which candidate maximizes program utility?",
            "required_input_refs": [ref("evidence_snapshot", "es1")],
            "resource_estimate": {"gpu_hours": gpu, "walltime_seconds": 10},
            "outcome_scenarios": [
                {"probability": .5, "posterior_utilities": {"A": 1, "B": 1 + gain}},
                {"probability": .5, "posterior_utilities": {"A": 1, "B": 0}},
            ],
            "p_decision_change": .5,
        }

    def test_selects_max_evsi_minus_explicit_cost(self):
        result = plan_actions(
            evidence_snapshot_ref=ref("evidence_snapshot", "es1"),
            current_utilities={"A": 1, "B": .8},
            candidates=[self.action("rbfe", "c1", 2, 4),
                        self.action("docking", "c2", 1, .1)],
            remaining_budget={"gpu_hours": 10, "walltime_seconds": 100},
            policy=self.policy, iteration=0)
        # The larger raw action is still chosen only after both actions are reduced to
        # the same utility-minus-priced-resource scale.
        self.assertEqual(result["decision"], "act")
        self.assertEqual(result["selected_action"]["action_kind"], "docking")
        self.assertGreater(result["selected_action"]["expected_net_value"], 0)

    def test_budget_and_anti_oscillation_are_hard_constraints(self):
        candidate = self.action("rbfe", "c1", 4, 1)
        previous = {**candidate,
                    "action_fingerprint": "placeholder"}
        # First obtain the normalized fingerprint, then replay it.
        first = plan_actions(
            evidence_snapshot_ref=ref("evidence_snapshot", "es1"),
            current_utilities={"A": 1, "B": .8}, candidates=[candidate],
            remaining_budget={"gpu_hours": 2, "walltime_seconds": 100},
            policy=self.policy, iteration=0)
        self.assertEqual(first["decision"], "act")
        previous["action_fingerprint"] = first["selected_action"]["action_fingerprint"]
        repeat = plan_actions(
            evidence_snapshot_ref=ref("evidence_snapshot", "es1"),
            current_utilities={"A": 1, "B": .8}, candidates=[candidate],
            remaining_budget={"gpu_hours": 2, "walltime_seconds": 100},
            policy=self.policy, iteration=1, action_history=[previous])
        self.assertEqual(repeat["excluded"][0]["reason_code"], "ANTI_OSCILLATION_REPEAT")


class ValidationAndResourceTests(unittest.TestCase):
    def test_coupled_chemical_state_mass_and_method_scoped_unsupported(self):
        from motif.chemical_states import build_state_ensemble, assess_method_support
        ensemble = build_state_ensemble(
            chemical_entity_ref=ref("chemical_entity", "c1"),
            condition={"ph": 7.4, "temperature_kelvin": 298.15, "solvent": "water"},
            engine={"name": "fixture", "version": "1", "release_digest": "sha256:" + "a" * 64, "parameters": {}},
            microstates=[{
                "microstate_ref": ref("chemical_microstate", "s1"),
                "protonation_key": "p1", "tautomer_key": "t1", "stereo_key": "r",
                "population": .9, "population_uncertainty": .05, "score": 1.0,
            }, {
                "microstate_ref": ref("chemical_microstate", "s2"),
                "protonation_key": "p2", "tautomer_key": "t2", "stereo_key": "r",
                "population": .08, "population_uncertainty": .03, "score": 2.0,
            }], maximum_states=8, minimum_population=.1, confidence="medium")
        self.assertAlmostEqual(ensemble["retained_population_mass"], .9)
        self.assertAlmostEqual(ensemble["discarded_population_mass"], .1)
        support = assess_method_support(
            chemical_entity_ref=ref("chemical_entity", "c1"),
            microstate_ref=ref("chemical_microstate", "s1"),
            method_release_ref=ref("method_release", "rbfe1"), system_type="metal",
            capability_contract={"metal": "unsupported"})
        self.assertFalse(support["global_chemical_disposition_changed"])

    def test_docking_expands_cluster_representatives_receptors_and_seeds(self):
        from motif.docking import plan_docking_expansion
        plan = plan_docking_expansion(
            microstate_refs=[ref("chemical_microstate", "s1")],
            conformer_ensembles={"s1": {"conformers": [
                {"conformer_id": 1, "rank": 0, "cluster": 0,
                 "relative_energy": 0, "converged": True},
                {"conformer_id": 2, "rank": 1, "cluster": 0,
                 "relative_energy": 1, "converged": True},
                {"conformer_id": 3, "rank": 2, "cluster": 1,
                 "relative_energy": 2, "converged": True},
            ]}}, receptor_state_refs=[ref("prepared_receptor_state", "r1"),
                                      ref("prepared_receptor_state", "r2")],
            binding_site_ref=ref("binding_site_hypothesis", "b1"), seeds=[1, 2],
            representatives_per_state=2)
        self.assertEqual(plan["job_count"], 8)
        self.assertTrue(plan["cross_run_clustering_required"])

    def test_valid_ligand_departure_is_negative_evidence_not_invalid_trajectory(self):
        from motif.physics import assess_md_trajectory
        result = assess_md_trajectory(
            trajectory_ref=ref("artifact", "traj"),
            analysis_protocol_ref=ref("method_release", "analysis1"),
            selections={"ligand": "resname LIG", "protein": "protein"},
            pbc={"unwrap": True, "image": True, "reference_selection": "protein"},
            alignment={"selection": "protein and backbone", "mass_weighted": False},
            cutoffs={"contact_angstrom": 4.0},
            blocks={"length_frames": 100, "minimum_blocks": 3},
            finite_frame_fraction=1, energy_drift_kj_mol_ns=.1,
            ligand_departed=True, departure_time_ns=2.4, repeat_index=1,
            quality_limits={"minimum_finite_frame_fraction": .99,
                            "maximum_absolute_energy_drift_kj_mol_ns": 1})
        self.assertEqual(result["scientific_effect"], "accepted_negative_evidence")
        self.assertEqual(result["scientific_state"], "accepted")
        self.assertEqual(result["schema_version"], "3.0")

    def test_proposal_quotas_and_route_transitions_prevent_generator_capture(self):
        from motif.proposals import apply_proposal_quotas, advance_route_assessment
        proposals = [{
            "proposal_id": f"p{i}", "parents": [ref("compound", "parent")],
            "generation_trace": {"reaction": {"template_id": "t1"}},
        } for i in range(3)]
        accepted, report = apply_proposal_quotas(
            proposals, max_per_parent=2, max_per_template=5)
        self.assertEqual(len(accepted), 2)
        self.assertEqual(report["excluded"][0]["reason_code"], "PARENT_QUOTA_REACHED")
        advance_route_assessment("route_proposed", "plausibility_assessed")
        with self.assertRaisesRegex(ValueError, "invalid route"):
            advance_route_assessment("not_assessed", "supported")

    def test_full_rbfe_matrix_is_four_edges_two_legs_three_repeats_minimum(self):
        from motif.rbfe import expand_rbfe_execution_matrix
        network = {
            "compounds": [{"id": f"c{i}"} for i in range(4)],
            "edges": [{"edge_id": f"e{i}"} for i in range(4)],
        }
        matrix = expand_rbfe_execution_matrix(network)
        self.assertEqual(matrix["production_execution_count"], 24)
        self.assertFalse(matrix["pilot_included_in_production_count"])
        with self.assertRaisesRegex(ValueError, "at least three"):
            expand_rbfe_execution_matrix(network, repeats=2)

    def test_rbfe_pairs_legs_then_preserves_repeat_disagreement(self):
        from motif.rbfe import _pair_legs_and_repeats
        legs = []
        for repeat, complex_value in ((1, 2.0), (2, 3.0), (3, 1.0)):
            legs.extend([
                {"edge_id": "e1", "repeat_index": repeat, "leg": "complex",
                 "status": "completed", "dg_kcal_mol": complex_value,
                 "uncertainty_kcal_mol": .2},
                {"edge_id": "e1", "repeat_index": repeat, "leg": "solvent",
                 "status": "completed", "dg_kcal_mol": .5,
                 "uncertainty_kcal_mol": .2},
            ])
        edges, diagnostics = _pair_legs_and_repeats(legs)
        self.assertEqual(edges[0]["repeat_count"], 3)
        diagnostic = next(row for row in diagnostics if row["status"] == "aggregated")
        self.assertGreater(diagnostic["between_repeat_variance"], 0)

    def test_three_compounds_can_only_be_smoke(self):
        threshold = LifecycleThreshold(30, 3, 3, 20, .4, .3, .7)
        policy = ValidationPolicy("validation-1", {
            ReleaseLifecycle.SCIENTIFIC_CANDIDATE: threshold,
        })
        result = assess_release({
            "independent_compounds": 3, "independent_series": 1, "split_groups": 1,
            "effective_sample_size": 2, "censoring_fraction": 0,
            "label_noise": .1, "domain_coverage": 1,
            "independent_holdout": False, "specification_curve_complete": False,
        }, policy, ReleaseLifecycle.SCIENTIFIC_CANDIDATE)
        self.assertFalse(result["eligible"])
        self.assertFalse(result["claim_eligible"])
        self.assertTrue(result["sample_size_is_smoke_only"])

    def test_atomic_multi_resource_lease_fences_and_expires(self):
        now = datetime(2026, 8, 13, tzinfo=timezone.utc)
        broker = AtomicResourceBroker(
            {"cpu_cores": 16, "ram_bytes": 64, "gpus": 1,
             "gpu_vram_bytes": 32, "scratch_bytes": 100,
             "persistent_growth_bytes": 50, "process_slots": 10,
             "scf_slots": 2, "campaign_credits": 100},
            external_usage={"gpu_vram_bytes": 8})
        lease = broker.acquire("job-1", "campaign-1",
                               {"gpus": 1, "gpu_vram_bytes": 20,
                                "scratch_bytes": 80}, ttl_seconds=10,
                               backend="kubernetes", now=now)
        with self.assertRaises(InsufficientCapacity):
            broker.acquire("job-2", "campaign-1", {"gpus": 1}, ttl_seconds=10,
                           backend="kubernetes", now=now)
        self.assertEqual(broker.available(now=now + timedelta(seconds=11))["gpus"], 1)
        with self.assertRaisesRegex(RuntimeError, "stale"):
            broker.release(lease.lease_id, lease.fencing_token, now=now + timedelta(seconds=11))

    def test_new_machine_schemas_accept_valid_and_reject_unknown(self):
        fixtures = {
            "orthogonal-state.schema.json": {
                "execution": "succeeded", "applicability": "applicable",
                "scientific": "provisional", "disposition": "pending",
                "claim_eligibility": "ineligible_provisional_quality", "reason_codes": []},
            "method-outcome.schema.json": {
                "schema_version": "3.0", "outcome_id": UUID,
                "method_run_ref": ref("method_run", "r1"), "execution_state": "succeeded",
                "artifact_refs": [], "finished_at": NOW},
            "evidence-item.schema.json": {
                "schema_version": "3.0", "evidence_id": UUID, "kind": "scalar_estimate",
                "subject_ref": ref("compound", "c1"), "condition_ref": ref("condition", "pH7"),
                "method_release_ref": ref("method_release", "m1"),
                "outcome_ref": ref("method_outcome", "o1"),
                "payload": {"value": 1.2, "unit": "kcal/mol", "uncertainty": .3},
                "applicability": "applicable", "scientific_state": "provisional",
                "claim_eligibility": "ineligible_provisional_quality",
                "dependency_refs": [], "shared_assumption_refs": [], "supersedes": [],
                "stale": False, "created_at": NOW},
            "routing-action.schema.json": {
                "schema_version": "3.0", "routing_action_id": UUID,
                "evidence_snapshot_ref": ref("evidence_snapshot", "es1"),
                "action_kind": "compute", "fidelity_label": "F4",
                "subject_ref": ref("compound", "c1"), "scientific_question": "rank A and B",
                "required_input_refs": [], "outcome_model_release_ref": ref("model_release", "o1"),
                "expected_utility_delta": .5, "resource_estimate": {"gpu_hours": 2},
                "budget_lease_ref": None, "reason_codes": ["MAX_EXPECTED_NET_VALUE"],
                "policy_release_ref": ref("policy_release", "p1"), "created_at": NOW},
            "structured-error.schema.json": {
                "schema_version": "3.0", "error_id": UUID, "category": "numerical",
                "stage": "rbfe.production", "subject_ref": ref("transformation", "t1"),
                "retryability": "after_parameter_change", "scientific_effect": "missing_evidence",
                "recommended_action": "reparameterize", "cause_code": "POOR_OVERLAP",
                "severity": "error", "detail": {}, "occurred_at": NOW},
        }
        for name, fixture in fixtures.items():
            with self.subTest(name=name):
                schema = json.loads((ROOT / "contracts/domain/motif" / name).read_text())
                validator = validator_for(schema)
                self.assertEqual(list(validator.iter_errors(fixture)), [])
                self.assertNotEqual(list(validator.iter_errors({**fixture, "unknown": 1})), [])


if __name__ == "__main__":
    unittest.main()
