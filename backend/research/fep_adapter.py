"""Read and compile against the existing durable RBFE Campaign and RunSet state."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping

import failures
from research.context_builder import canonical_digest
from research.loop_repository import stage_request_key


_JOB_METHODS = (
    "physics.motif.rbfe_network", "physics.motif.rbfe_system_prepare",
)

_PROJECT_CONTEXT_FIELDS = {
    "campaign-question": "research_question",
    "assay-anchor": "assay_anchor",
    "portfolio-priority": "portfolio_priority",
    "pose-hypothesis": "pose_hypothesis",
    "cost-cap": "cost_cap",
    "next-action": "human_next_action",
    "stop-rule": "human_stop_rule",
    "target": "target",
    "target-name": "target",
    "campaign-name": "campaign_name",
    "reference-ligand": "reference_ligand",
    "reference-ligand-id": "reference_ligand",
}


def _bounded_text(value: Any, limit: int = 2048) -> str:
    """Keep user-owned decision context useful without copying raw large inputs."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    else:
        text = str(value)
    return text.strip()[:limit]


def _compound_priorities(value: Any) -> list[dict[str, str]]:
    """Parse the Workbench's durable pipe-delimited priority table."""
    rows: list[dict[str, str]] = []
    if isinstance(value, list):
        candidates = value
    else:
        candidates = str(value or "").splitlines()
    for item in candidates[:64]:
        if isinstance(item, Mapping):
            compound_id = _bounded_text(
                item.get("compound_id") or item.get("id"), 256)
            if not compound_id:
                continue
            rows.append({
                "compound_id": compound_id,
                "priority": _bounded_text(item.get("priority"), 64),
                "rationale": _bounded_text(item.get("rationale"), 512),
                "synthesis_status": _bounded_text(
                    item.get("synthesis_status") or item.get("status"), 128),
            })
            continue
        parts = [part.strip() for part in str(item).split("|")]
        if not parts or not parts[0]:
            continue
        parts += [""] * (4 - len(parts))
        rows.append({
            "compound_id": parts[0][:256], "priority": parts[1][:64],
            "rationale": parts[2][:512], "synthesis_status": parts[3][:128],
        })
    return rows


def _project_context(campaign: Mapping[str, Any]) -> dict[str, Any]:
    """Extract only bounded decision fields already saved by the FEP Workbench."""
    state = campaign.get("state") or {}
    client = ((state.get("client_state") or {}).get("values") or {})
    result: dict[str, Any] = {}
    for source, target in _PROJECT_CONTEXT_FIELDS.items():
        if source in client and target not in result:
            text = _bounded_text(client[source])
            if text:
                result[target] = text
    priorities = _compound_priorities(client.get("compound-priorities"))
    if priorities:
        result["compound_priorities"] = priorities
    return result


def _ref(kind: str, identifier: Any) -> dict[str, str]:
    return {"kind": kind, "id": str(identifier)}


def _artifact_ref(identifier: Any, digest: str) -> dict[str, str]:
    return {"kind": "artifact", "id": str(identifier),
            "sha256": digest if digest.startswith("sha256:") else "sha256:" + digest}


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    text = str(value or "")
    if text:
        return text
    return "1970-01-01T00:00:00Z"


class FepAdapter:
    """Adapter over real RBFE repositories; it owns no second scientific state."""

    def __init__(self, references: Any, runsets: Any, connect: Any) -> None:
        self.references = references
        self.runsets = runsets
        self._connect = connect

    def assess_bootstrap(self, loop: Mapping[str, Any]) -> dict[str, Any]:
        try:
            campaign = self.references.get_campaign(
                str(loop["campaign_id"]), self._actor(loop))
        except failures.DiracNotFound:
            return {
                "ready": False, "reason_code": "HUMAN_WORKBENCH_REQUIRED",
                "summary": "The linked Campaign has no durable RBFE campaign with the same identity.",
            }
        if campaign["status"] in {"stale", "archived"}:
            return {
                "ready": False, "reason_code": "CAMPAIGN_STALE",
                "summary": "The RBFE campaign is stale or archived and cannot authorize new work.",
            }
        if campaign["status"] not in {"poses_reviewed", "planned"}:
            return {
                "ready": False, "reason_code": "HUMAN_WORKBENCH_REQUIRED",
                "summary": "Human pose review must be completed in the existing FEP Workbench.",
            }
        return {"ready": True, "campaign": campaign}

    def snapshot(self, loop: Mapping[str, Any]) -> dict[str, Any]:
        durable = self._durable_snapshot(loop)
        campaign = durable["campaign"]
        network = durable["network"]
        prepared_edges = durable["prepared_edges"]
        runsets = durable["runsets"]
        systems = durable["systems"]
        campaign_id = str(loop["campaign_id"])
        campaign_source = _ref("campaign", campaign_id)
        project_context = _project_context(campaign)
        priority_by_compound = {
            row["compound_id"]: row
            for row in project_context.get("compound_priorities", [])
        }
        objects: list[dict[str, Any]] = [{
            "ref": campaign_source,
            "label": str(campaign["state"].get("label") or f"FEP Campaign {campaign_id[:8]}"),
            "state": {
                "status": campaign["status"], "version": campaign["version"],
                "scientific_generation": campaign["campaign_scientific_generation"],
                "scientific_digest": campaign["campaign_scientific_digest"],
                "project_context": project_context,
            },
        }]
        facts: list[dict[str, Any]] = [self._fact(
            "campaign-currentness", "campaign_state", "system_state",
            campaign_source, campaign_source,
            {"status": campaign["status"], "version": campaign["version"]},
            campaign["campaign_scientific_generation"],
            status="server_current", eligible=False,
            reasons=["SYSTEM_STATE_NOT_SCIENTIFIC_EVIDENCE"], priority=1000,
        )]
        if project_context:
            facts.append(self._fact(
                "campaign-project-context", "project_decision_context", "system_state",
                campaign_source, campaign_source, project_context,
                campaign["campaign_scientific_generation"],
                status="human_authored_project_context", eligible=False,
                reasons=["PROJECT_CONTEXT_NOT_SCIENTIFIC_EVIDENCE"], priority=990,
            ))
        for system in systems:
            receptor_ref = dict(system["prepared_receptor_state_ref"])
            objects.append({
                "ref": {"kind": receptor_ref["kind"], "id": receptor_ref["id"]},
                "label": str(system["label"]),
                "state": {
                    "execution_eligible": bool(system["execution_eligible"]),
                    "campaign_scope": system["campaign_scope"],
                    "preparation_state": system["preparation_state"],
                    "pose_count": len(system.get("poses") or []),
                },
            })
        if network is not None:
            network_ref = network["ref"]
            objects.append({
                "ref": {"kind": "artifact", "id": network_ref["id"]},
                "label": "Current governed RBFE network",
                "state": {"digest": network["document"]["digest"],
                          "edge_count": len(network["document"].get("edges") or [])},
            })
            for edge in network["document"].get("edges") or []:
                edge_id = str(edge["edge_id"])
                subject = _ref("free_energy_transformation", edge_id)
                prepared = prepared_edges.get(edge_id)
                endpoint_context = {
                    endpoint: priority_by_compound[compound_id]
                    for endpoint, compound_id in (
                        ("left", str(edge.get("left_id") or "")),
                        ("right", str(edge.get("right_id") or "")),
                    ) if compound_id in priority_by_compound
                }
                objects.append({
                    "ref": subject, "label": f"{edge.get('left_id')} → {edge.get('right_id')}",
                    "state": {
                        "left_id": edge.get("left_id"),
                        "right_id": edge.get("right_id"),
                        "prepared": prepared is not None,
                        "mapping_score": edge.get("mapping_score"),
                        "mapping_warnings": edge.get("mapping_warnings") or [],
                        "endpoint_project_context": endpoint_context,
                    },
                })
                facts.append(self._fact(
                    f"network-edge:{edge_id}", "network_edge", "method_result",
                    network_ref, subject,
                    {key: edge.get(key) for key in (
                        "left_id", "right_id", "mapping_score", "mapping_warnings")} | {
                            "endpoint_project_context": endpoint_context},
                    campaign["campaign_scientific_generation"],
                    status="governed_execution_plan", eligible=False,
                    reasons=["NETWORK_PLAN_NOT_SCIENTIFIC_EVIDENCE"], priority=700,
                ))
                if prepared is not None:
                    facts.append(self._fact(
                        f"prepared-edge:{edge_id}", "prepared_edge", "method_result",
                        prepared["edge_spec_ref"], subject,
                        {"validation_status": prepared["result"].get("validation_status"),
                         "mapping_score": (prepared["result"].get("system_build") or {}).get(
                             "mapping_score")},
                        campaign["campaign_scientific_generation"],
                        status="server_preflight_passed", eligible=False,
                        reasons=["PREPARED_INPUT_NOT_SCIENTIFIC_EVIDENCE"], priority=800,
                    ))
        for runset in runsets:
            subject = _ref("free_energy_transformation", runset["edge_id"])
            objects.append({
                "ref": dict(runset["ref"]),
                "label": f"RBFE RunSet {runset['edge_id']}",
                "state": {"state": runset["state"], "edge_id": runset["edge_id"],
                          "attention": runset.get("attention") or {}},
            })
            if runset["state"] == "completed":
                facts.append(self._fact(
                    f"runset:{runset['ref']['id']}", "fep_result", "method_result",
                    runset["edge_spec_ref"], subject,
                    {"aggregate": runset.get("aggregate_output") or {},
                     "runset_ref": runset["ref"]},
                    runset["campaign_scientific_ref"]["version"],
                    status="completed_unvalidated", eligible=False,
                    reasons=["METHOD_RESULT_NOT_EVIDENCE", "QUALITY_PROJECTION_REQUIRED"],
                    priority=950,
                ))
        human_attestations = self._human_attestations(campaign, systems)
        action_history = durable["action_history"]
        available_actions = self._available_actions(
            network, prepared_edges, systems, campaign_id)
        attention = []
        if network is None:
            attention.append({
                "reason_code": "NETWORK_ABSENT",
                "summary": "No current governed RBFE network Artifact exists for this Campaign.",
            })
        if not any(system.get("execution_eligible") for system in systems):
            attention.append({
                "reason_code": "HUMAN_WORKBENCH_REQUIRED",
                "summary": "No reviewed execution-eligible receptor/pose system is available.",
            })
        return {
            "campaign_binding": {
                "campaign_scientific_generation": campaign[
                    "campaign_scientific_generation"],
                "campaign_scientific_digest": campaign["campaign_scientific_digest"],
                "campaign_status": campaign["status"],
                "state_digest": campaign["state_digest"],
            },
            "objects": objects, "facts": facts,
            "human_attestations": human_attestations,
            "action_history": action_history,
            "available_actions": available_actions,
            "open_attention": attention,
            "goal_constraints": [
                "Use only listed bounded action templates.",
                "Never promote Method results to scientific Evidence.",
                "Physical FEP RunSets require named human per-action approval.",
            ],
            "success_definition": [
                "A new governed result changes the next action or closes the loop.",
                "Stopping is explicit when no valid information-gaining action remains.",
            ],
            "source_clock": durable["source_clock"],
        }

    def resolve(self, *, template_id: str, candidate: Mapping[str, Any],
                loop: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, Any]:
        durable = self._durable_snapshot(loop)
        campaign = durable["campaign"]
        source_versions = self._base_source_versions(campaign, durable["network"])
        if context["campaign_binding"]["state_digest"] != campaign["state_digest"]:
            raise failures.DiracStalePreview(
                "Campaign changed before action compilation")
        if template_id in {"fep.stop.v1", "fep.defer_for_experiment.v1"}:
            return {"command_input": None, "source_versions": source_versions,
                    "estimate": {"available": True, "gpu_hours_upper_bound": 0,
                                 "external_cost_upper_bound": 0}}
        edge_id = str((candidate.get("parameter_hints") or {}).get("edge_id") or "")
        subject = candidate.get("subject_ref") or {}
        if subject != _ref("free_energy_transformation", edge_id):
            raise failures.DiracModelOutputInvalid(
                "edge hint does not identify the selected current subject")
        attempt = int((loop.get("stage_attempts") or {}).get("dispatch", 0))
        request_key = stage_request_key(
            str(loop["run_id"]), int(loop["iteration"]), "dispatch", attempt)
        if template_id == "fep.run_selected_edge.v1":
            prepared = durable["prepared_edges"].get(edge_id)
            if prepared is None:
                raise failures.DiracInvalidParameters(
                    "selected edge is not prepared; insert prepare_selected_edge first")
            source_versions["edge_spec_digest"] = prepared["result"]["spec_digest"]
            return {
                "command_input": {
                    "request_key": request_key,
                    "campaign_id": str(loop["campaign_id"]),
                    "campaign_scientific_generation": campaign[
                        "campaign_scientific_generation"],
                    "campaign_scientific_digest": campaign[
                        "campaign_scientific_digest"],
                    "edge_spec_ref": prepared["edge_spec_ref"],
                    "edge_network_ref": prepared["edge_network_ref"],
                    "complex_transformation_ref": prepared["complex_transformation_ref"],
                    "solvent_transformation_ref": prepared["solvent_transformation_ref"],
                },
                "source_versions": source_versions,
                "estimate": {"available": True, "gpu_hours_upper_bound": 4.0,
                             "external_cost_upper_bound": 0},
                "consequence_summary": (
                    "Starts complex and solvent legs across three repeats for one edge."),
            }
        if template_id == "fep.prepare_selected_edge.v1":
            network = durable["network"]
            if network is None:
                raise failures.DiracInvalidParameters("no current RBFE network exists")
            if edge_id in durable["prepared_edges"]:
                raise failures.DiracInvalidParameters("selected edge is already prepared")
            edge = next((row for row in network["document"].get("edges") or []
                         if row.get("edge_id") == edge_id), None)
            if edge is None:
                raise failures.DiracInvalidParameters("selected edge is absent from current network")
            system = next((row for row in durable["systems"]
                           if row.get("execution_eligible")), None)
            if system is None:
                raise failures.DiracInvalidParameters(
                    "no reviewed execution-eligible receptor/pose system exists")
            pose_by_smiles = {row.get("canonical_smiles"): row for row in system["poses"]}
            compound_by_id = {row["id"]: row for row in network["document"]["compounds"]}
            try:
                parent = pose_by_smiles[compound_by_id[edge["left_id"]]["smiles"]]
                proposal = pose_by_smiles[compound_by_id[edge["right_id"]]["smiles"]]
            except KeyError as error:
                raise failures.DiracInvalidParameters(
                    "selected edge endpoints do not resolve to reviewed endpoint poses") from error
            return {
                "command_input": {
                    "campaign_id": str(loop["campaign_id"]),
                    "campaign_scientific_generation": campaign[
                        "campaign_scientific_generation"],
                    "campaign_scientific_digest": campaign[
                        "campaign_scientific_digest"],
                    "network_ref": network["ref"], "edge_id": edge_id,
                    "prepared_receptor_state_ref": system[
                        "prepared_receptor_state_ref"],
                    "parent_pose_ref": parent["pose_ref"],
                    "proposal_pose_ref": proposal["pose_ref"],
                    "protocol_preset": "openfe-rfe-standard-v1",
                },
                "source_versions": source_versions,
                "estimate": {"available": True, "gpu_hours_upper_bound": 0,
                             "external_cost_upper_bound": 0},
            }
        if template_id == "fep.replan_network.v1":
            system = next((row for row in durable["systems"]
                           if row.get("execution_eligible")), None)
            inputs = campaign["state"].get("inputs") or campaign["state"]
            compounds = inputs.get("compounds") or []
            if system is None or len(compounds) < 2:
                raise failures.DiracInvalidParameters(
                    "network planning requires reviewed system and at least two compounds")
            return {
                "command_input": {
                    "compounds": [{"id": str(row["id"]), "smiles": str(row["smiles"])}
                                  for row in compounds],
                    "campaign_id": str(loop["campaign_id"]),
                    "campaign_scientific_generation": campaign[
                        "campaign_scientific_generation"],
                    "campaign_scientific_digest": campaign[
                        "campaign_scientific_digest"],
                    "prepared_system_id": system["prepared_receptor_state_ref"]["id"],
                    "mode": "pilot", "planner": "openfe",
                },
                "source_versions": source_versions,
                "estimate": {"available": True, "gpu_hours_upper_bound": 0,
                             "external_cost_upper_bound": 0},
            }
        raise failures.DiracUnsupported("unimplemented FEP action template")

    def current_source_versions(self, loop: Mapping[str, Any],
                                pending: Mapping[str, Any]) -> dict[str, Any]:
        durable = self._durable_snapshot(loop)
        versions = self._base_source_versions(durable["campaign"], durable["network"])
        edge_id = ((pending.get("preview") or {}).get("subject_ref") or {}).get("id")
        prepared = durable["prepared_edges"].get(edge_id)
        if prepared is not None:
            versions["edge_spec_digest"] = prepared["result"]["spec_digest"]
        return versions

    def _durable_snapshot(self, loop: Mapping[str, Any]) -> dict[str, Any]:
        actor = self._actor(loop)
        campaign = self.references.get_campaign(str(loop["campaign_id"]), actor)
        systems = self.references.list_systems(actor, str(loop["campaign_id"]), False)
        jobs = self._jobs(str(loop["campaign_id"]), actor)
        network = self._latest_network(jobs)
        prepared = self._prepared_edges(jobs)
        runsets = self._runsets(str(loop["campaign_id"]), actor)
        action_history = self._action_history(str(loop["run_id"]))
        seen = {item["action_fingerprint"] for item in action_history}
        for receipt in (loop.get("outputs") or {}).get("action_receipts") or []:
            fingerprint = receipt.get("action_fingerprint")
            if not fingerprint or fingerprint in seen:
                continue
            action_history.append({
                "action_fingerprint": fingerprint,
                "template_id": receipt.get("template_id"),
                "subject_ref": receipt.get("subject_ref"),
                "result": "completed",
                "human_rejected": False,
            })
            seen.add(fingerprint)
        clocks = [campaign.get("updated_at")]
        clocks.extend(item.get("finished_at") or item.get("updated_at") for item in runsets)
        clocks.extend(item.get("finished_at") for item in jobs)
        current_clock = max((_iso(value) for value in clocks if value is not None),
                            default="1970-01-01T00:00:00Z")
        return {"campaign": campaign, "systems": systems, "jobs": jobs,
                "network": network, "prepared_edges": prepared, "runsets": runsets,
                "action_history": action_history, "source_clock": current_clock}

    def _jobs(self, campaign_id: str, actor: Mapping[str, str]) -> list[dict[str, Any]]:
        placeholders = ",".join(["%s"] * len(_JOB_METHODS))
        query = (
            "SELECT j.id::text,m.method_id::text,j.state::text,j.params,j.result_summary,"
            "j.error_code::text,j.finished_at,a.id::text,ja.role,encode(a.blob_sha256,'hex') "
            "FROM app.job j JOIN meta.method m ON m.id=j.method_row_id "
            "LEFT JOIN app.job_artifact ja ON ja.job_id=j.id "
            "LEFT JOIN app.artifact a ON a.id=ja.artifact_id "
            f"WHERE m.method_id IN ({placeholders}) AND j.actor_kind=%s AND j.actor_id=%s "
            "AND j.params->>'campaign_id'=%s ORDER BY j.created_at DESC,ja.ordinal")
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(query, (*_JOB_METHODS, actor["kind"], actor["id"], campaign_id))
            rows = cur.fetchall()
        jobs: dict[str, dict[str, Any]] = {}
        for row in rows:
            item = jobs.setdefault(row[0], {
                "id": row[0], "method_id": row[1], "state": row[2],
                "parameters": dict(row[3] or {}),
                "result_summary": dict(row[4] or {}), "error_code": row[5],
                "finished_at": row[6], "artifacts": {},
            })
            if row[7] is not None:
                item["artifacts"][row[8]] = _artifact_ref(row[7], row[9])
        return list(jobs.values())

    def _latest_network(self, jobs: list[Mapping[str, Any]]) -> dict[str, Any] | None:
        for job in jobs:
            if (job["method_id"] == "physics.motif.rbfe_network"
                    and job["state"] == "done" and "rbfe.network" in job["artifacts"]):
                reference = job["artifacts"]["rbfe.network"]
                document = self._read_json_artifact(reference, "rbfe.network")
                if document.get("digest") != canonical_digest(
                        {key: value for key, value in document.items() if key != "digest"}):
                    raise failures.DiracInternal("RBFE network Artifact digest is invalid")
                return {"ref": reference, "document": document, "job_id": job["id"]}
        return None

    def _prepared_edges(self, jobs: list[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
        prepared = {}
        required = {
            "rbfe.edge_spec": "edge_spec_ref", "rbfe.edge_network": "edge_network_ref",
            "rbfe.openfe.complex_transformation": "complex_transformation_ref",
            "rbfe.openfe.solvent_transformation": "solvent_transformation_ref",
        }
        for job in jobs:
            if job["method_id"] != "physics.motif.rbfe_system_prepare" or job["state"] != "done":
                continue
            data = (job.get("result_summary") or {}).get("data") or {}
            edge_id = data.get("edge_id")
            if not edge_id or not set(required).issubset(job["artifacts"]):
                continue
            prepared[str(edge_id)] = {
                "job_id": job["id"], "result": dict(data),
                **{field: job["artifacts"][role] for role, field in required.items()},
            }
        return prepared

    def _runsets(self, campaign_id: str,
                 actor: Mapping[str, str]) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id::text FROM app.rbfe_run_set WHERE actor_kind=%s AND actor_id=%s "
                "AND specification->>'campaign_id'=%s ORDER BY created_at",
                (actor["kind"], actor["id"], campaign_id),
            )
            identifiers = [row[0] for row in cur.fetchall()]
        return [self.runsets.get(identifier, dict(actor)) for identifier in identifiers]

    def _action_history(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT encode(p.action_fingerprint,'hex'),p.decision,b.bytes "
                "FROM app.research_loop_approval p "
                "JOIN app.artifact a ON a.id=p.preview_artifact_id "
                "JOIN app.blob b ON b.sha256=a.blob_sha256 WHERE p.run_id=%s "
                "ORDER BY p.created_at", (run_id,),
            )
            rows = cur.fetchall()
        history = []
        for fingerprint, decision, raw in rows:
            preview = json.loads(bytes(raw))
            history.append({
                "action_fingerprint": "sha256:" + fingerprint,
                "template_id": preview["template_id"],
                "subject_ref": preview["subject_ref"],
                "result": decision,
                "human_rejected": decision == "rejected",
            })
        return history

    def _read_json_artifact(self, reference: Mapping[str, Any], role: str) -> dict:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT a.role,encode(a.blob_sha256,'hex'),b.bytes FROM app.artifact a "
                "JOIN app.blob b ON b.sha256=a.blob_sha256 WHERE a.id=%s",
                (reference["id"],),
            )
            row = cur.fetchone()
        if row is None or row[0] != role:
            raise failures.DiracInternal(f"missing durable {role} Artifact")
        raw = bytes(row[2])
        digest = hashlib.sha256(raw).hexdigest()
        if digest != row[1] or reference["sha256"] != "sha256:" + digest:
            raise failures.DiracInternal(f"durable {role} Artifact failed digest verification")
        return json.loads(raw)

    @staticmethod
    def _available_actions(network: Mapping[str, Any] | None,
                           prepared: Mapping[str, Any],
                           systems: list[Mapping[str, Any]],
                           campaign_id: str) -> list[dict[str, Any]]:
        actions = []
        campaign_subjects = [_ref("campaign", campaign_id)]
        if network is None and any(row.get("execution_eligible") for row in systems):
            actions.append({
                "template_id": "fep.replan_network.v1",
                "subject_refs": campaign_subjects,
                "intent": "Build a current governed RBFE network.", "risk_class": "R2",
            })
        if network is not None:
            for edge in network["document"].get("edges") or []:
                subject = _ref("free_energy_transformation", edge["edge_id"])
                template = ("fep.run_selected_edge.v1" if edge["edge_id"] in prepared
                            else "fep.prepare_selected_edge.v1")
                actions.append({
                    "template_id": template, "subject_refs": [subject],
                    "intent": ("Run the qualified edge." if edge["edge_id"] in prepared
                               else "Prepare the selected edge before execution."),
                    "risk_class": "R3" if edge["edge_id"] in prepared else "R2",
                })
        actions.extend([
            {"template_id": "fep.stop.v1", "subject_refs": campaign_subjects,
             "intent": "Stop with an explicit governed receipt.", "risk_class": "R0"},
            {"template_id": "fep.defer_for_experiment.v1", "subject_refs": campaign_subjects,
             "intent": "Draft a follow-up without external execution.", "risk_class": "R0"},
        ])
        return actions

    @staticmethod
    def _human_attestations(campaign: Mapping[str, Any],
                            systems: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
        actor = campaign["created_by"]
        rows = []
        for system in systems:
            accepted = [pose for pose in system.get("poses") or []
                        if pose.get("review_state") == "accepted"]
            if accepted:
                rows.append({
                    "attestation_id": (
                        f"pose-review:{system['prepared_receptor_state_ref']['id']}"),
                    "actor_ref": _ref(actor["kind"], actor["id"]),
                    "subject_ref": _ref(
                        "prepared_receptor_state",
                        system["prepared_receptor_state_ref"]["id"]),
                    "status": "accepted",
                    "created_at": _iso(campaign.get("updated_at")),
                })
        return rows

    @staticmethod
    def _fact(fact_id: str, category: str, source_class: str,
              source_ref: Mapping[str, Any], subject_ref: Mapping[str, Any],
              value: Mapping[str, Any], generation: int | None, *, status: str,
              eligible: bool, reasons: list[str], priority: int) -> dict[str, Any]:
        return {
            "_priority": priority, "fact_id": fact_id, "category": category,
            "source_class": source_class, "source_ref": dict(source_ref),
            "subject_ref": dict(subject_ref), "condition_ref": None,
            "structured_value": dict(value),
            "freshness": {"stale": False, "source_generation": generation},
            "claim_boundary": {"status": status,
                               "eligible_as_scientific_evidence": eligible,
                               "reason_codes": reasons},
        }

    @staticmethod
    def _base_source_versions(campaign: Mapping[str, Any],
                              network: Mapping[str, Any] | None) -> dict[str, Any]:
        versions = {
            "campaign_version": campaign["version"],
            "campaign_scientific_generation": campaign[
                "campaign_scientific_generation"],
            "campaign_scientific_digest": campaign["campaign_scientific_digest"],
            "network_digest": (None if network is None
                               else network["document"]["digest"]),
        }
        return versions

    @staticmethod
    def _actor(loop: Mapping[str, Any]) -> dict[str, str]:
        return {"kind": str(loop["actor_kind"]), "id": str(loop["actor_id"])}
