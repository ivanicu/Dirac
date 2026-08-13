"""Program repositories with one aggregate transaction per mutation."""
from __future__ import annotations

import copy
import hashlib
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable

import failures
from . import domain as D


def _ref(kind: str, identifier: Any) -> dict[str, str]:
    return {"kind": kind, "id": str(identifier)}


def _event_ref(identifier: Any) -> dict[str, str]:
    return _ref("artifact", identifier)


def _version(value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise failures.DiracInvalidParameters("expected_version must be a positive integer")
    return value


def _program_health(program: dict[str, Any]) -> dict[str, Any]:
    """Transparent, rule-based health; never disguises a heuristic as science."""
    current = lambda name: [item for item in program.get(name, [])
                            if item.get("status") not in {"superseded", "retired", "cancelled"}]
    objectives = current("objectives"); hypotheses = current("hypotheses")
    members = current("members"); gates = current("stage_gates")
    work = current("work_items") or current("work_packages"); evidence = current("evidence_bindings")
    checks = [
        ("target", bool(program.get("target_ref")), "Assign the canonical target."),
        ("portfolio", bool(program.get("portfolio_ref")), "Place the Program in a Portfolio."),
        ("lead", any(item.get("role") == "program_lead" for item in members), "Assign a Program lead."),
        ("objective", bool(objectives), "Record at least one explicit objective."),
        ("hypothesis", bool(hypotheses), "Record at least one falsifiable hypothesis."),
        ("stage_gate", any(item.get("stage") == program.get("stage") for item in gates),
         "Define the current stage gate."),
        ("evidence", bool(evidence), "Attach evidence to a claim, hypothesis, decision, or gate."),
        ("delivery", bool(work), "Create an owned scientific work package."),
    ]
    met = sum(1 for _key, passed, _action in checks if passed)
    blocked = [item for item in work if item.get("status") == "blocked"]
    rejected = [item for item in gates if item.get("status") == "rejected"]
    risks = [{"code": key, "severity": "high" if key in {"target", "lead", "objective"} else "medium",
              "action": action} for key, passed, action in checks if not passed]
    risks.extend({"code": "blocked_work", "severity": "high",
                  "action": f"Resolve blocked work package {item.get('key')}."} for item in blocked)
    risks.extend({"code": "rejected_gate", "severity": "high",
                  "action": f"Resolve rejected stage gate {item.get('key')}."} for item in rejected)
    return {"score": round(100 * met / len(checks)), "status": "healthy" if met == len(checks) and not blocked
            else "at_risk" if blocked or rejected or met < len(checks) / 2 else "needs_attention",
            "basis": "rule-based-operational-readiness-v1", "checks": [
                {"key": key, "passed": passed, "action": action} for key, passed, action in checks],
            "risks": risks, "counts": {"blocked_work": len(blocked), "rejected_gates": len(rejected)}}


class MemoryProgramRepository:
    """Semantically faithful process-local implementation for focused tests."""

    kind = "memory"
    durability = "process"

    def __init__(self) -> None:
        self.programs: dict[str, dict[str, Any]] = {}
        self.by_code: dict[str, str] = {}
        self.portfolios: dict[str, dict[str, Any]] = {}
        self.portfolio_by_code: dict[str, str] = {}
        self.events: dict[str, list[dict[str, Any]]] = {}
        self.request_results: dict[tuple[str, str], dict[str, Any]] = {}
        self.snapshots: dict[str, dict[str, Any]] = {}

    def create(self, value: dict, actor: dict, request_id: str | None = None) -> dict:
        spec = D.create_spec(value); who = D.actor(actor)
        existing_id = self.by_code.get(spec["code"])
        if existing_id is not None:
            existing = self.programs[existing_id]
            if existing["name"] == spec["name"]:
                return {"program": self._overview(existing_id), "created": False}
            raise failures.DiracInvalidParameters(
                "Program code already belongs to a different Program",
                details={"code": spec["code"], "program_ref": _ref("program", existing_id)})
        identifier = str(uuid.uuid4()); now = datetime.now(timezone.utc).isoformat()
        row = {
            "ref": _ref("program", identifier), "id": identifier,
            **{k: copy.deepcopy(v) for k, v in spec.items() if k != "target_ref"},
            "target_ref": copy.deepcopy(spec["target_ref"]), "version": 1,
            "created_at": now, "updated_at": now, "updated_by": who,
            "objectives": [], "hypotheses": [], "decisions": [],
            "milestones": [], "links": [], "members": [], "stage_gates": [],
            "work_items": [], "work_packages": [], "work_transitions": [],
            "work_executions": [], "evidence_bindings": [], "lineage": [],
        }
        self.programs[identifier] = row; self.by_code[spec["code"]] = identifier
        self.events[identifier] = []
        result = {"program": self._overview(identifier), "created": True}
        event = self._event(identifier, 1, "program.created", None, result, who, request_id)
        result["event_ref"] = _event_ref(event["id"])
        event["payload"]["result"] = copy.deepcopy(result)
        return result

    def get(self, program_ref: dict) -> dict:
        return {"program": self._overview(self._id(program_ref))}

    def list(self, *, lifecycle: str | None = None, limit: int = 100) -> dict:
        if lifecycle is not None and lifecycle not in D.LIFECYCLES:
            raise failures.DiracInvalidParameters("unknown Program lifecycle")
        rows = [self._summary(p) for p in self.programs.values()
                if lifecycle is None or p["lifecycle"] == lifecycle]
        rows.sort(key=lambda p: (p["updated_at"], p["code"]), reverse=True)
        return {"programs": rows[:max(1, min(int(limit), 500))]}

    def create_portfolio(self, value: dict, actor: dict, request_id: str | None = None) -> dict:
        spec = D.portfolio(value); who = D.actor(actor)
        existing_id = self.portfolio_by_code.get(spec["code"])
        if existing_id:
            return {"portfolio": copy.deepcopy(self.portfolios[existing_id]), "created": False}
        identifier = str(uuid.uuid4()); now = datetime.now(timezone.utc).isoformat()
        item = {"ref": _ref("portfolio", identifier), **spec, "version": 1,
                "created_at": now, "updated_at": now, "updated_by": who}
        self.portfolios[identifier] = item; self.portfolio_by_code[spec["code"]] = identifier
        return {"portfolio": copy.deepcopy(item), "created": True}

    def list_portfolios(self, limit: int = 100) -> dict:
        rows = sorted(self.portfolios.values(), key=lambda item: (item["updated_at"], item["code"]), reverse=True)
        return {"portfolios": copy.deepcopy(rows[:max(1, min(int(limit), 500))])}

    def update(self, program_ref: dict, expected_version: int, patch: dict,
               actor: dict, request_id: str | None = None) -> dict:
        identifier = self._id(program_ref); who = D.actor(actor)
        duplicate = self._duplicate(identifier, request_id)
        if duplicate is not None: return duplicate
        row = self.programs[identifier]; self._expect(row, expected_version)
        changes = D.update_patch(row, patch)
        row.update(copy.deepcopy(changes)); row["version"] += 1
        row["updated_at"] = datetime.now(timezone.utc).isoformat(); row["updated_by"] = who
        result = {"program": self._overview(identifier), "changed_fields": sorted(changes)}
        return self._record(identifier, "program.updated", None, result, who, request_id)

    def record_objective(self, program_ref: dict, expected_version: int, value: dict,
                         actor: dict, request_id: str | None = None) -> dict:
        return self._record_atom(program_ref, expected_version, "objective", D.objective(value), actor, request_id)

    def record_hypothesis(self, program_ref: dict, expected_version: int, value: dict,
                          actor: dict, request_id: str | None = None) -> dict:
        return self._record_atom(program_ref, expected_version, "hypothesis", D.hypothesis(value), actor, request_id)

    def record_decision(self, program_ref: dict, expected_version: int, value: dict,
                        actor: dict, request_id: str | None = None) -> dict:
        return self._record_atom(program_ref, expected_version, "decision", D.decision(value), actor, request_id)

    def record_milestone(self, program_ref: dict, expected_version: int, value: dict,
                         actor: dict, request_id: str | None = None) -> dict:
        return self._record_atom(program_ref, expected_version, "milestone", D.milestone(value), actor, request_id)


    def assign_portfolio(self, program_ref: dict, expected_version: int, portfolio_ref: dict,
                         actor: dict, request_id: str | None = None) -> dict:
        identifier = self._id(program_ref); who = D.actor(actor); target = D.ref(portfolio_ref, "portfolio")
        if target["id"] not in self.portfolios:
            raise failures.DiracNotFound("Portfolio does not exist", details={"portfolio_ref": target})
        duplicate = self._duplicate(identifier, request_id)
        if duplicate is not None: return duplicate
        row = self.programs[identifier]; self._expect(row, expected_version)
        row["portfolio_ref"] = target; row["version"] += 1
        result = {"portfolio_ref": target, "program_version": row["version"]}
        return self._record(identifier, "portfolio.assigned", target, result, who, request_id)

    def assign_member(self, program_ref: dict, expected_version: int, value: dict,
                      actor: dict, request_id: str | None = None) -> dict:
        return self._record_unique(program_ref, expected_version, "member", D.member(value), actor,
                                   request_id, lambda item: (item["principal"], item["role"]),
                                   "member.assigned")

    def record_stage_gate(self, program_ref: dict, expected_version: int, value: dict,
                          actor: dict, request_id: str | None = None) -> dict:
        return self._record_revision(program_ref, expected_version, "stage_gate", D.stage_gate(value),
                                     actor, request_id, "stage_gates")

    def record_work_package(self, program_ref: dict, expected_version: int, value: dict,
                            actor: dict, request_id: str | None = None) -> dict:
        identifier = self._id(program_ref); who = D.actor(actor); item = D.work_package(value)
        duplicate = self._duplicate(identifier, request_id)
        if duplicate is not None: return duplicate
        row = self.programs[identifier]; self._expect(row, expected_version)
        work_item = next((entry for entry in row["work_items"]
                          if entry["key"].lower() == item["key"].lower()), None)
        created = work_item is None
        if work_item is None:
            work_item = {"ref": _ref("work_item", uuid.uuid4()), "key": item["key"],
                         "title": item["title"], "lane": item["lane"],
                         "created_by": who, "created_at": datetime.now(timezone.utc).isoformat(),
                         "transitions": [], "executions": []}
            row["work_items"].append(work_item)
            transition = {"ref": _ref("artifact", uuid.uuid4()), "work_item_ref": work_item["ref"],
                          "from_lane": None, "to_lane": item["lane"],
                          "reason": "Created in this workflow lane", "transitioned_by": who,
                          "transitioned_at": datetime.now(timezone.utc).isoformat()}
            work_item["transitions"].append(transition); row["work_transitions"].append(transition)
        current = next((package for package in reversed(row["work_packages"])
                        if package["work_item_ref"] == work_item["ref"]
                        and package.get("status") != "superseded"), None)
        if current: current["status"] = "superseded"
        package = {"ref": _ref("work_package", uuid.uuid4()), "work_item_ref": work_item["ref"],
                   **copy.deepcopy(item), "lane": work_item["lane"],
                   "revision": current["revision"] + 1 if current else 1,
                   "supersedes_ref": current["ref"] if current else None,
                   "created_by": who, "created_at": datetime.now(timezone.utc).isoformat()}
        row["work_packages"].append(package); work_item["title"] = package["title"]
        work_item["current_package"] = copy.deepcopy(package); work_item["status"] = package["status"]
        row["version"] += 1
        result = {"work_item": copy.deepcopy(work_item), "work_package": copy.deepcopy(package),
                  "program_version": row["version"], "created": created}
        return self._record(identifier, "work_package.recorded", work_item["ref"], result, who, request_id)

    def transition_work_item(self, program_ref: dict, expected_version: int, value: dict,
                             actor: dict, request_id: str | None = None) -> dict:
        identifier = self._id(program_ref); who = D.actor(actor); transition = D.work_transition(value)
        duplicate = self._duplicate(identifier, request_id)
        if duplicate is not None: return duplicate
        row = self.programs[identifier]; self._expect(row, expected_version)
        work_item = next((entry for entry in row["work_items"]
                          if entry["ref"] == transition["work_item_ref"]), None)
        if work_item is None: raise failures.DiracNotFound("Program Work Item does not exist")
        if work_item["lane"] == transition["to_lane"]:
            raise failures.DiracInvalidParameters("Work Item is already in that workflow lane")
        record = {"ref": _ref("artifact", uuid.uuid4()), **transition,
                  "from_lane": work_item["lane"], "transitioned_by": who,
                  "transitioned_at": datetime.now(timezone.utc).isoformat()}
        work_item["lane"] = transition["to_lane"]
        work_item["transitions"].append(record); row["work_transitions"].append(record)
        row["version"] += 1
        result = {"work_item": copy.deepcopy(work_item), "transition": copy.deepcopy(record),
                  "program_version": row["version"]}
        return self._record(identifier, "work_item.transitioned", work_item["ref"], result, who, request_id)

    def attach_work_execution(self, program_ref: dict, expected_version: int, value: dict,
                              actor: dict, request_id: str | None = None) -> dict:
        identifier = self._id(program_ref); who = D.actor(actor); binding = D.work_execution(value)
        duplicate = self._duplicate(identifier, request_id)
        if duplicate is not None: return duplicate
        row = self.programs[identifier]; self._expect(row, expected_version)
        work_item = next((entry for entry in row["work_items"]
                          if entry["ref"] == binding["work_item_ref"]), None)
        if work_item is None: raise failures.DiracNotFound("Program Work Item does not exist")
        existing = next((entry for entry in row["work_executions"]
                         if entry["job_ref"] == binding["job_ref"]), None)
        if existing:
            if existing["work_item_ref"] != binding["work_item_ref"]:
                raise failures.DiracInvalidParameters("runtime Job already belongs to another Work Item")
            return {"execution": copy.deepcopy(existing), "program_version": row["version"], "created": False}
        execution = {"ref": _ref("artifact", uuid.uuid4()), **binding, "linked_by": who,
                     "linked_at": datetime.now(timezone.utc).isoformat()}
        row["work_executions"].append(execution); work_item["executions"].append(execution)
        row["version"] += 1
        result = {"execution": copy.deepcopy(execution), "program_version": row["version"], "created": True}
        return self._record(identifier, "work_execution.linked", work_item["ref"], result, who, request_id)

    def attach_evidence(self, program_ref: dict, expected_version: int, value: dict,
                        actor: dict, request_id: str | None = None) -> dict:
        return self._record_unique(program_ref, expected_version, "evidence_binding",
                                   D.evidence_binding(value), actor, request_id,
                                   lambda item: (item["subject_ref"], item["relation"], item["evidence_ref"]),
                                   "evidence.attached")

    def record_lineage(self, program_ref: dict, expected_version: int, value: dict,
                       actor: dict, request_id: str | None = None) -> dict:
        return self._record_unique(program_ref, expected_version, "lineage", D.lineage(value), actor,
                                   request_id, lambda item: (item["source_ref"], item["relation"], item["target_ref"]),
                                   "lineage.recorded")

    def health(self, program_ref: dict) -> dict:
        program = self._overview(self._id(program_ref))
        return {"health": _program_health(program)}

    def link(self, program_ref: dict, expected_version: int, object_ref: dict, role: str,
             rationale: str | None, actor: dict, request_id: str | None = None) -> dict:
        identifier = self._id(program_ref); who = D.actor(actor); target = D.ref(object_ref)
        if target["kind"] == "program":
            raise failures.DiracInvalidParameters("a Program cannot link another Program as a child object")
        link_role = D.key(role, "role")
        duplicate = self._duplicate(identifier, request_id)
        if duplicate is not None: return duplicate
        row = self.programs[identifier]; self._expect(row, expected_version)
        for link in row["links"]:
            if link["object_ref"] == target and link["role"] == link_role:
                return {"link": copy.deepcopy(link), "program_version": row["version"], "created": False}
        row["version"] += 1
        link = {"ref": _ref("artifact", uuid.uuid4()), "object_ref": target,
                "role": link_role, "rationale": rationale, "linked_by": who,
                "linked_at": datetime.now(timezone.utc).isoformat()}
        row["links"].append(link)
        result = {"link": copy.deepcopy(link), "program_version": row["version"], "created": True}
        return self._record(identifier, "object.linked", target, result, who, request_id)

    def create_snapshot(self, program_ref: dict, expected_version: int,
                        actor: dict, request_id: str | None = None) -> dict:
        identifier = self._id(program_ref); who = D.actor(actor)
        duplicate = self._duplicate(identifier, request_id)
        if duplicate is not None: return duplicate
        row = self.programs[identifier]; self._expect(row, expected_version); row["version"] += 1
        document = self._overview(identifier); document["snapshot_version"] = row["version"]
        digest = D.digest(document); snapshot_id = str(uuid.uuid4())
        snapshot = {"ref": _ref("program_snapshot", snapshot_id), "program_ref": _ref("program", identifier),
                    "program_version": row["version"], "digest": digest, "document": document,
                    "created_by": who, "created_at": datetime.now(timezone.utc).isoformat()}
        self.snapshots[snapshot_id] = copy.deepcopy(snapshot)
        result = {"snapshot": snapshot}
        return self._record(identifier, "snapshot.created", snapshot["ref"], result, who, request_id)

    def _record_revision(self, program_ref, expected_version, kind, value, actor, request_id, collection_name):
        identifier = self._id(program_ref); who = D.actor(actor)
        duplicate = self._duplicate(identifier, request_id)
        if duplicate is not None: return duplicate
        row = self.programs[identifier]; self._expect(row, expected_version)
        collection = row[collection_name]
        current = next((item for item in reversed(collection)
                        if item["key"].lower() == value["key"].lower()
                        and item.get("status") != "superseded"), None)
        if current: current["status"] = "superseded"
        row["version"] += 1
        atom = {"ref": _ref(kind, uuid.uuid4()), **copy.deepcopy(value),
                "revision": current["revision"] + 1 if current else 1,
                "supersedes_ref": current["ref"] if current else None,
                "created_by": who, "created_at": datetime.now(timezone.utc).isoformat()}
        collection.append(atom)
        result = {kind: copy.deepcopy(atom), "program_version": row["version"]}
        return self._record(identifier, f"{kind}.recorded", atom["ref"], result, who, request_id)

    def _record_unique(self, program_ref, expected_version, kind, value, actor, request_id,
                       identity, event_kind):
        identifier = self._id(program_ref); who = D.actor(actor)
        duplicate = self._duplicate(identifier, request_id)
        if duplicate is not None: return duplicate
        row = self.programs[identifier]; self._expect(row, expected_version)
        collection_name = {"member":"members", "evidence_binding":"evidence_bindings", "lineage":"lineage"}[kind]
        collection = row[collection_name]; marker = identity(value)
        existing = next((item for item in collection if identity(item) == marker), None)
        if existing:
            return {kind: copy.deepcopy(existing), "program_version": row["version"], "created": False}
        row["version"] += 1
        item = {"ref": _ref("artifact", uuid.uuid4()),
                **copy.deepcopy(value), "created_by": who,
                "created_at": datetime.now(timezone.utc).isoformat()}
        collection.append(item)
        result = {kind: copy.deepcopy(item), "program_version": row["version"], "created": True}
        return self._record(identifier, event_kind, item["ref"], result, who, request_id)

    def _record_atom(self, program_ref: dict, expected_version: int, kind: str,
                     value: dict, actor: dict, request_id: str | None) -> dict:
        identifier = self._id(program_ref); who = D.actor(actor)
        duplicate = self._duplicate(identifier, request_id)
        if duplicate is not None: return duplicate
        row = self.programs[identifier]; self._expect(row, expected_version)
        collection_name = {"objective": "objectives", "hypothesis": "hypotheses",
                           "decision": "decisions", "milestone": "milestones"}[kind]
        collection = row[collection_name]; current = next((a for a in reversed(collection)
                                                        if a["key"].lower() == value["key"].lower()
                                                        and a.get("status", "active") == "active"), None)
        revision = (current["revision"] + 1) if current else 1
        if current is not None and kind != "decision": current["status"] = "superseded"
        row["version"] += 1; atom_id = str(uuid.uuid4())
        atom = {"ref": _ref(kind, atom_id), **copy.deepcopy(value), "revision": revision,
                "supersedes_ref": current["ref"] if current else None,
                "status": "active" if kind != "decision" else "recorded",
                "created_by": who, "created_at": datetime.now(timezone.utc).isoformat()}
        collection.append(atom)
        result = {kind: copy.deepcopy(atom), "program_version": row["version"]}
        return self._record(identifier, f"{kind}.recorded", atom["ref"], result, who, request_id)

    def _record(self, identifier: str, kind: str, atom_ref: dict | None, result: dict,
                who: dict, request_id: str | None) -> dict:
        event = self._event(identifier, self.programs[identifier]["version"], kind,
                            atom_ref, result, who, request_id)
        output = copy.deepcopy(result); output["event_ref"] = _event_ref(event["id"])
        event["payload"]["result"] = copy.deepcopy(output)
        if request_id: self.request_results[(identifier, request_id)] = copy.deepcopy(output)
        return output

    def _event(self, identifier: str, version: int, kind: str, atom_ref: dict | None,
               result: dict, who: dict, request_id: str | None) -> dict:
        event = {"id": str(uuid.uuid4()), "kind": kind, "program_version": version,
                 "atom_ref": atom_ref, "payload": {"result": copy.deepcopy(result)},
                 "actor": who, "request_id": request_id,
                 "occurred_at": datetime.now(timezone.utc).isoformat()}
        self.events[identifier].append(event)
        return event

    def _id(self, value: dict) -> str:
        identifier = D.ref(value, "program")["id"]
        if identifier not in self.programs:
            raise failures.DiracNotFound("Program does not exist", details={"program_ref": value})
        return identifier

    def _expect(self, row: dict, expected: int) -> None:
        expected = _version(expected)
        if row["version"] != expected:
            raise failures.DiracInvalidParameters("Program version conflict",
                details={"expected_version": expected, "current_version": row["version"]})

    def _duplicate(self, identifier: str, request_id: str | None) -> dict | None:
        return copy.deepcopy(self.request_results.get((identifier, request_id))) if request_id else None

    def _summary(self, row: dict) -> dict:
        return {k: copy.deepcopy(row.get(k)) for k in
                ("ref", "code", "name", "summary", "lifecycle", "stage", "version",
                 "target_ref", "owner_id", "updated_at")}

    def _overview(self, identifier: str) -> dict:
        row = self.programs[identifier]
        base = {k: copy.deepcopy(v) for k, v in row.items()
                if k not in {"id", "objectives", "hypotheses", "decisions", "milestones", "links",
                             "members", "stage_gates", "work_items", "work_packages", "work_transitions",
                             "work_executions", "evidence_bindings", "lineage"}}
        base.update({k: copy.deepcopy(row[k]) for k in
                     ("objectives", "hypotheses", "decisions", "milestones", "links", "members",
                      "stage_gates", "work_items", "work_packages", "work_transitions",
                      "work_executions", "evidence_bindings", "lineage")})
        base["counts"] = {k: len(row[k]) for k in
                          ("objectives", "hypotheses", "decisions", "milestones", "links", "members",
                           "stage_gates", "work_items", "work_packages", "work_transitions",
                           "work_executions", "evidence_bindings", "lineage")}
        base["health"] = _program_health(base)
        base["events"] = copy.deepcopy(self.events.get(identifier, [])[-30:][::-1])
        return base


class PostgresProgramRepository:
    """Durable Program repository; relational state and event journal commit together."""

    kind = "postgres"
    durability = "durable"

    def __init__(self, connect: Callable[[], Any]) -> None:
        self._connect = connect

    @staticmethod
    def _cursor(conn):
        from psycopg.rows import dict_row
        return conn.cursor(row_factory=dict_row)

    @staticmethod
    def _json(value: Any):
        from psycopg.types.json import Jsonb
        return Jsonb(D.jsonable(value))

    def create(self, value: dict, actor: dict, request_id: str | None = None) -> dict:
        spec = D.create_spec(value); who = D.actor(actor)
        with self._connect() as conn, self._cursor(conn) as cur:
            cur.execute("SELECT id,name FROM design.project WHERE code=%s", (spec["code"],))
            existing = cur.fetchone()
            if existing:
                if existing["name"] == spec["name"]:
                    return {"program": self._overview(cur, existing["id"]), "created": False}
                raise failures.DiracInvalidParameters("Program code already belongs to a different Program")
            target_id = spec["target_ref"]["id"] if spec["target_ref"] else None
            portfolio_id = spec["portfolio_ref"]["id"] if spec["portfolio_ref"] else None
            archived_at = datetime.now(timezone.utc) if spec["lifecycle"] == "archived" else None
            cur.execute(
                "INSERT INTO design.project(code,name,target_id,portfolio_id,lifecycle,stage,summary,indication,modality,owner_id,"
                "archived_at,updated_by_kind,updated_by_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "RETURNING id",
                (spec["code"], spec["name"], target_id, portfolio_id, spec["lifecycle"], spec["stage"],
                 spec["summary"], spec["indication"], spec["modality"], spec["owner_id"],
                 archived_at, who["kind"], who["id"]))
            identifier = cur.fetchone()["id"]
            result = {"program": self._overview(cur, identifier), "created": True}
            event_id = self._insert_event(cur, identifier, 1, "program.created", None,
                                          result, who, request_id)
            result["event_ref"] = _event_ref(event_id)
            self._update_event_result(cur, event_id, result)
            return D.jsonable(result)

    def get(self, program_ref: dict) -> dict:
        identifier = D.ref(program_ref, "program")["id"]
        with self._connect() as conn, self._cursor(conn) as cur:
            return {"program": self._overview(cur, identifier)}

    def list(self, *, lifecycle: str | None = None, limit: int = 100) -> dict:
        if lifecycle is not None and lifecycle not in D.LIFECYCLES:
            raise failures.DiracInvalidParameters("unknown Program lifecycle")
        limit = max(1, min(int(limit), 500))
        where = "WHERE lifecycle=%s" if lifecycle else ""
        args = (lifecycle, limit) if lifecycle else (limit,)
        with self._connect() as conn, self._cursor(conn) as cur:
            cur.execute(
                "SELECT id,code::text,name,summary,lifecycle::text,stage::text,version,target_id,portfolio_id,owner_id,updated_at "
                f"FROM design.project {where} ORDER BY updated_at DESC,code LIMIT %s", args)
            return {"programs": [self._summary(row) for row in cur.fetchall()]}

    def create_portfolio(self, value: dict, actor: dict, request_id: str | None = None) -> dict:
        spec = D.portfolio(value); who = D.actor(actor)
        with self._connect() as conn, self._cursor(conn) as cur:
            cur.execute("SELECT id,code::text,name,mandate,lifecycle,version,created_at,updated_at,updated_by_kind::text,updated_by_id FROM design.portfolio WHERE code=%s", (spec["code"],))
            row = cur.fetchone()
            if row:
                return {"portfolio": self._portfolio(row), "created": False}
            cur.execute("INSERT INTO design.portfolio(code,name,mandate,lifecycle,updated_by_kind,updated_by_id) "
                        "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id,code::text,name,mandate,lifecycle,version,created_at,updated_at,updated_by_kind::text,updated_by_id",
                        (spec["code"], spec["name"], spec["mandate"], spec["lifecycle"], who["kind"], who["id"]))
            return {"portfolio": self._portfolio(cur.fetchone()), "created": True}

    def list_portfolios(self, limit: int = 100) -> dict:
        limit = max(1, min(int(limit), 500))
        with self._connect() as conn, self._cursor(conn) as cur:
            cur.execute("SELECT id,code::text,name,mandate,lifecycle,version,created_at,updated_at,updated_by_kind::text,updated_by_id FROM design.portfolio ORDER BY updated_at DESC,code LIMIT %s", (limit,))
            return {"portfolios": [self._portfolio(row) for row in cur.fetchall()]}

    def update(self, program_ref: dict, expected_version: int, patch: dict,
               actor: dict, request_id: str | None = None) -> dict:
        who = D.actor(actor)
        with self._mutation(program_ref, expected_version, request_id) as state:
            cur, identifier, row, duplicate = state
            if duplicate is not None: return duplicate
            changes = D.update_patch(self._program(row), patch)
            assignments = []; values = []
            for field, value in changes.items():
                column = {"target_ref":"target_id", "portfolio_ref":"portfolio_id"}.get(field, field)
                if field in {"target_ref", "portfolio_ref"}: value = value["id"] if value else None
                assignments.append(f"{column}=%s"); values.append(value)
            if "lifecycle" in changes:
                assignments.append("archived_at=%s")
                values.append(datetime.now(timezone.utc) if changes["lifecycle"] == "archived" else None)
            assignments.extend(["version=version+1", "updated_at=now()", "updated_by_kind=%s", "updated_by_id=%s"])
            values.extend([who["kind"], who["id"], identifier])
            cur.execute(f"UPDATE design.project SET {','.join(assignments)} WHERE id=%s RETURNING version", values)
            version = cur.fetchone()["version"]
            result = {"program": self._overview(cur, identifier), "changed_fields": sorted(changes)}
            return self._finish(cur, identifier, version, "program.updated", None, result, who, request_id)

    def record_objective(self, program_ref: dict, expected_version: int, value: dict,
                         actor: dict, request_id: str | None = None) -> dict:
        return self._record_atom(program_ref, expected_version, "objective", D.objective(value), actor, request_id)

    def record_hypothesis(self, program_ref: dict, expected_version: int, value: dict,
                          actor: dict, request_id: str | None = None) -> dict:
        return self._record_atom(program_ref, expected_version, "hypothesis", D.hypothesis(value), actor, request_id)

    def record_decision(self, program_ref: dict, expected_version: int, value: dict,
                        actor: dict, request_id: str | None = None) -> dict:
        return self._record_atom(program_ref, expected_version, "decision", D.decision(value), actor, request_id)

    def record_milestone(self, program_ref: dict, expected_version: int, value: dict,
                         actor: dict, request_id: str | None = None) -> dict:
        return self._record_atom(program_ref, expected_version, "milestone", D.milestone(value), actor, request_id)

    def assign_portfolio(self, program_ref: dict, expected_version: int, portfolio_ref: dict,
                         actor: dict, request_id: str | None = None) -> dict:
        target = D.ref(portfolio_ref, "portfolio"); who = D.actor(actor)
        with self._mutation(program_ref, expected_version, request_id) as state:
            cur, identifier, _row, duplicate = state
            if duplicate is not None: return duplicate
            self._require_entity(cur, target)
            cur.execute("UPDATE design.project SET portfolio_id=%s,version=version+1,updated_at=now(),updated_by_kind=%s,updated_by_id=%s WHERE id=%s RETURNING version",
                        (target["id"], who["kind"], who["id"], identifier))
            version = cur.fetchone()["version"]
            result = {"portfolio_ref": target, "program_version": version}
            return self._finish(cur, identifier, version, "portfolio.assigned", target, result, who, request_id)

    def assign_member(self, program_ref: dict, expected_version: int, value: dict,
                      actor: dict, request_id: str | None = None) -> dict:
        item = D.member(value); who = D.actor(actor); principal = item["principal"]
        with self._mutation(program_ref, expected_version, request_id) as state:
            cur, identifier, row, duplicate = state
            if duplicate is not None: return duplicate
            cur.execute("SELECT id,responsibility,assigned_at,assigned_by_kind::text,assigned_by_id FROM design.program_member WHERE program_id=%s AND principal_kind=%s AND principal_id=%s AND role=%s AND retired_at IS NULL",
                        (identifier, principal["kind"], principal["id"], item["role"]))
            existing = cur.fetchone()
            if existing:
                return {"member": self._member(existing, principal, item["role"]), "program_version": row["version"], "created": False}
            cur.execute("INSERT INTO design.program_member(program_id,principal_kind,principal_id,role,responsibility,assigned_by_kind,assigned_by_id) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id,responsibility,assigned_at,assigned_by_kind::text,assigned_by_id",
                        (identifier, principal["kind"], principal["id"], item["role"], item["responsibility"], who["kind"], who["id"]))
            member = self._member(cur.fetchone(), principal, item["role"]); version = self._advance(cur, identifier, who)
            result = {"member": member, "program_version": version, "created": True}
            return self._finish(cur, identifier, version, "member.assigned", None, result, who, request_id)

    def record_stage_gate(self, program_ref: dict, expected_version: int, value: dict,
                          actor: dict, request_id: str | None = None) -> dict:
        item = D.stage_gate(value); who = D.actor(actor)
        with self._mutation(program_ref, expected_version, request_id) as state:
            cur, identifier, _row, duplicate = state
            if duplicate is not None: return duplicate
            cur.execute("SELECT id,revision FROM design.program_stage_gate WHERE program_id=%s AND gate_key=%s ORDER BY revision DESC LIMIT 1 FOR UPDATE", (identifier, item["key"]))
            old = cur.fetchone(); revision = old["revision"] + 1 if old else 1
            if old: cur.execute("UPDATE design.program_stage_gate SET status='superseded' WHERE id=%s", (old["id"],))
            assessed_at = datetime.now(timezone.utc) if item["status"] in {"approved", "rejected"} else None
            decision_id = item["decision_ref"]["id"] if item["decision_ref"] else None
            cur.execute("INSERT INTO design.program_stage_gate(program_id,gate_key,revision,stage,title,criteria,status,evidence_summary,decision_id,target_date,assessed_at,supersedes_id,created_by_kind,created_by_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,created_at",
                        (identifier, item["key"], revision, item["stage"], item["title"], self._json(item["criteria"]), item["status"], item["evidence_summary"], decision_id, item["target_date"], assessed_at, old["id"] if old else None, who["kind"], who["id"]))
            inserted = cur.fetchone(); gate = {"ref": _ref("stage_gate", inserted["id"]), **item, "revision": revision,
                "supersedes_ref": _ref("stage_gate", old["id"]) if old else None, "created_by": who, "created_at": inserted["created_at"]}
            version = self._advance(cur, identifier, who); result = {"stage_gate": gate, "program_version": version}
            return self._finish(cur, identifier, version, "stage_gate.recorded", gate["ref"], result, who, request_id)

    def record_work_package(self, program_ref: dict, expected_version: int, value: dict,
                            actor: dict, request_id: str | None = None) -> dict:
        item = D.work_package(value); who = D.actor(actor)
        with self._mutation(program_ref, expected_version, request_id) as state:
            cur, identifier, _row, duplicate = state
            if duplicate is not None: return duplicate
            cur.execute("SELECT id,current_lane::text,current_package_id,created_at,created_by_kind::text,created_by_id FROM design.program_work_item WHERE program_id=%s AND work_key=%s FOR UPDATE", (identifier, item["key"]))
            work_row = cur.fetchone(); created = work_row is None
            if work_row is None:
                cur.execute("INSERT INTO design.program_work_item(program_id,work_key,title,current_lane,created_by_kind,created_by_id) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id,current_lane::text,current_package_id,created_at,created_by_kind::text,created_by_id",
                            (identifier, item["key"], item["title"], item["lane"], who["kind"], who["id"]))
                work_row = cur.fetchone()
                cur.execute("INSERT INTO design.program_work_transition(work_item_id,from_lane,to_lane,reason,transitioned_by_kind,transitioned_by_id) VALUES (%s,NULL,%s,%s,%s,%s)",
                            (work_row["id"], item["lane"], "Created in this workflow lane", who["kind"], who["id"]))
            elif work_row["current_lane"] != item["lane"]:
                raise failures.DiracInvalidParameters(
                    "Work Item lane changes require program.work_item.transition",
                    details={"work_item_ref": _ref("work_item", work_row["id"]),
                             "current_lane": work_row["current_lane"], "requested_lane": item["lane"]})
            old = None
            if work_row["current_package_id"]:
                cur.execute("SELECT id,revision FROM design.program_work_package WHERE id=%s FOR UPDATE",
                            (work_row["current_package_id"],)); old = cur.fetchone()
            revision = old["revision"] + 1 if old else 1
            if old: cur.execute("UPDATE design.program_work_package SET status='superseded' WHERE id=%s", (old["id"],))
            owner = item["owner"]
            cur.execute("INSERT INTO design.program_work_package(program_id,work_item_id,work_key,revision,title,description,status,priority,owner_kind,owner_id,due_on,deliverable_refs,supersedes_id,created_by_kind,created_by_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,created_at",
                        (identifier, work_row["id"], item["key"], revision, item["title"], item["description"], item["status"], item["priority"], owner["kind"] if owner else None, owner["id"] if owner else None, item["due_on"], self._json(item["deliverable_refs"]), old["id"] if old else None, who["kind"], who["id"]))
            inserted = cur.fetchone()
            for dependency in item["depends_on_refs"]:
                cur.execute("SELECT program_id FROM design.program_work_item WHERE id=%s", (dependency["id"],))
                dependency_row = cur.fetchone()
                if dependency_row is None or str(dependency_row["program_id"]) != str(identifier):
                    raise failures.DiracInvalidParameters("Work Item dependencies must belong to the same Program")
                cur.execute("INSERT INTO design.program_work_item_dependency(work_item_id,depends_on_work_item_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                            (work_row["id"], dependency["id"]))
            cur.execute("UPDATE design.program_work_item SET title=%s,current_package_id=%s WHERE id=%s",
                        (item["title"], inserted["id"], work_row["id"]))
            work_ref = _ref("work_item", work_row["id"])
            package = {"ref": _ref("work_package", inserted["id"]), "work_item_ref": work_ref,
                **item, "lane": work_row["current_lane"], "revision": revision,
                "supersedes_ref": _ref("work_package", old["id"]) if old else None, "created_by": who, "created_at": inserted["created_at"]}
            work_item = {"ref": work_ref, "key": item["key"], "title": item["title"],
                         "lane": work_row["current_lane"], "status": item["status"],
                         "current_package": package, "created_at": work_row["created_at"],
                         "created_by": {"kind": work_row["created_by_kind"], "id": work_row["created_by_id"]}}
            version = self._advance(cur, identifier, who)
            result = {"work_item": work_item, "work_package": package,
                      "program_version": version, "created": created}
            return self._finish(cur, identifier, version, "work_package.recorded", work_ref, result, who, request_id)

    def transition_work_item(self, program_ref: dict, expected_version: int, value: dict,
                             actor: dict, request_id: str | None = None) -> dict:
        item = D.work_transition(value); who = D.actor(actor)
        with self._mutation(program_ref, expected_version, request_id) as state:
            cur, identifier, _row, duplicate = state
            if duplicate is not None: return duplicate
            cur.execute("SELECT id,work_key::text AS key,title,current_lane::text,current_package_id,created_at,created_by_kind::text,created_by_id FROM design.program_work_item WHERE id=%s AND program_id=%s FOR UPDATE",
                        (item["work_item_ref"]["id"], identifier))
            row = cur.fetchone()
            if row is None: raise failures.DiracNotFound("Program Work Item does not exist")
            if row["current_lane"] == item["to_lane"]:
                raise failures.DiracInvalidParameters("Work Item is already in that workflow lane")
            cur.execute("INSERT INTO design.program_work_transition(work_item_id,from_lane,to_lane,reason,transitioned_by_kind,transitioned_by_id) VALUES (%s,%s,%s,%s,%s,%s) RETURNING id,transitioned_at",
                        (row["id"], row["current_lane"], item["to_lane"], item["reason"], who["kind"], who["id"]))
            moved = cur.fetchone()
            cur.execute("UPDATE design.program_work_item SET current_lane=%s WHERE id=%s", (item["to_lane"], row["id"]))
            transition = {"ref": _ref("artifact", moved["id"]), "work_item_ref": item["work_item_ref"],
                          "from_lane": row["current_lane"], "to_lane": item["to_lane"],
                          "reason": item["reason"], "transitioned_at": moved["transitioned_at"],
                          "transitioned_by": who}
            work_item = {"ref": item["work_item_ref"], "key": row["key"], "title": row["title"],
                         "lane": item["to_lane"]}
            version = self._advance(cur, identifier, who)
            result = {"work_item": work_item, "transition": transition, "program_version": version}
            return self._finish(cur, identifier, version, "work_item.transitioned", item["work_item_ref"], result, who, request_id)

    def attach_work_execution(self, program_ref: dict, expected_version: int, value: dict,
                              actor: dict, request_id: str | None = None) -> dict:
        item = D.work_execution(value); who = D.actor(actor)
        with self._mutation(program_ref, expected_version, request_id) as state:
            cur, identifier, row, duplicate = state
            if duplicate is not None: return duplicate
            cur.execute("SELECT id FROM design.program_work_item WHERE id=%s AND program_id=%s",
                        (item["work_item_ref"]["id"], identifier))
            if cur.fetchone() is None: raise failures.DiracNotFound("Program Work Item does not exist")
            cur.execute("SELECT id,work_item_id,purpose,linked_at,linked_by_kind::text,linked_by_id FROM design.program_work_execution WHERE job_id=%s",
                        (item["job_ref"]["id"],))
            existing = cur.fetchone()
            if existing:
                if str(existing["work_item_id"]) != item["work_item_ref"]["id"]:
                    raise failures.DiracInvalidParameters("runtime Job already belongs to another Work Item")
                execution = self._work_execution(existing, item["job_ref"])
                return {"execution": execution, "program_version": row["version"], "created": False}
            cur.execute("INSERT INTO design.program_work_execution(work_item_id,job_id,purpose,linked_by_kind,linked_by_id) VALUES (%s,%s,%s,%s,%s) RETURNING id,work_item_id,purpose,linked_at,linked_by_kind::text,linked_by_id",
                        (item["work_item_ref"]["id"], item["job_ref"]["id"], item["purpose"], who["kind"], who["id"]))
            execution = self._work_execution(cur.fetchone(), item["job_ref"])
            version = self._advance(cur, identifier, who)
            result = {"execution": execution, "program_version": version, "created": True}
            return self._finish(cur, identifier, version, "work_execution.linked", item["work_item_ref"], result, who, request_id)

    def attach_evidence(self, program_ref: dict, expected_version: int, value: dict,
                        actor: dict, request_id: str | None = None) -> dict:
        item = D.evidence_binding(value); who = D.actor(actor); subject = item["subject_ref"]; evidence = item["evidence_ref"]
        with self._mutation(program_ref, expected_version, request_id) as state:
            cur, identifier, row, duplicate = state
            if duplicate is not None: return duplicate
            self._require_entity(cur, subject); self._require_entity(cur, evidence)
            cur.execute("SELECT id,claim,strength,attached_at,attached_by_kind::text,attached_by_id FROM design.program_evidence_binding WHERE program_id=%s AND subject_kind=%s AND subject_id=%s AND relation=%s AND evidence_kind=%s AND evidence_id=%s",
                        (identifier, subject["kind"], subject["id"], item["relation"], evidence["kind"], evidence["id"]))
            existing = cur.fetchone()
            if existing:
                return {"evidence_binding": self._evidence(existing, item), "program_version": row["version"], "created": False}
            cur.execute("INSERT INTO design.program_evidence_binding(program_id,subject_kind,subject_id,relation,evidence_kind,evidence_id,claim,strength,attached_by_kind,attached_by_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,claim,strength,attached_at,attached_by_kind::text,attached_by_id",
                        (identifier, subject["kind"], subject["id"], item["relation"], evidence["kind"], evidence["id"], item["claim"], item["strength"], who["kind"], who["id"]))
            binding = self._evidence(cur.fetchone(), item); version = self._advance(cur, identifier, who)
            result = {"evidence_binding": binding, "program_version": version, "created": True}
            return self._finish(cur, identifier, version, "evidence.attached", evidence, result, who, request_id)

    def record_lineage(self, program_ref: dict, expected_version: int, value: dict,
                       actor: dict, request_id: str | None = None) -> dict:
        item = D.lineage(value); who = D.actor(actor); source = item["source_ref"]; target = item["target_ref"]
        with self._mutation(program_ref, expected_version, request_id) as state:
            cur, identifier, row, duplicate = state
            if duplicate is not None: return duplicate
            self._require_entity(cur, source); self._require_entity(cur, target)
            cur.execute("SELECT id,created_at,actor_kind::text,actor_id FROM app.object_relation WHERE source_kind=%s AND source_id=%s AND relation=%s AND target_kind=%s AND target_id=%s",
                        (source["kind"], source["id"], item["relation"], target["kind"], target["id"]))
            edge_row = cur.fetchone(); created = edge_row is None
            if created:
                cur.execute("INSERT INTO app.object_relation(source_kind,source_id,relation,target_kind,target_id,actor_kind,actor_id) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id,created_at,actor_kind::text,actor_id",
                            (source["kind"], source["id"], item["relation"], target["kind"], target["id"], who["kind"], who["id"]))
                edge_row = cur.fetchone()
                for object_ref, role in ((source, "lineage-source"), (target, "lineage-target")):
                    cur.execute("INSERT INTO design.program_object_link(program_id,object_kind,object_id,role,linked_by_kind,linked_by_id) VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                                (identifier, object_ref["kind"], object_ref["id"], role, who["kind"], who["id"]))
            edge = {"ref": _ref("artifact", edge_row["id"]), **item, "created_at": edge_row["created_at"], "created_by": {"kind": edge_row["actor_kind"], "id": edge_row["actor_id"]}}
            if not created: return {"lineage": edge, "program_version": row["version"], "created": False}
            version = self._advance(cur, identifier, who); result = {"lineage": edge, "program_version": version, "created": True}
            return self._finish(cur, identifier, version, "lineage.recorded", None, result, who, request_id)

    def health(self, program_ref: dict) -> dict:
        identifier = D.ref(program_ref, "program")["id"]
        with self._connect() as conn, self._cursor(conn) as cur:
            return {"health": _program_health(self._overview(cur, identifier))}

    def link(self, program_ref: dict, expected_version: int, object_ref: dict, role: str,
             rationale: str | None, actor: dict, request_id: str | None = None) -> dict:
        target = D.ref(object_ref); link_role = D.key(role, "role"); who = D.actor(actor)
        if target["kind"] == "program":
            raise failures.DiracInvalidParameters("a Program cannot link another Program as a child object")
        with self._mutation(program_ref, expected_version, request_id) as state:
            cur, identifier, _row, duplicate = state
            if duplicate is not None: return duplicate
            self._require_entity(cur, target)
            cur.execute("SELECT id,linked_at,linked_by_kind::text,linked_by_id,rationale FROM design.program_object_link "
                        "WHERE program_id=%s AND object_kind=%s AND object_id=%s AND role=%s AND retired_at IS NULL",
                        (identifier, target["kind"], target["id"], link_role))
            existing = cur.fetchone()
            if existing:
                return {"link": self._link(existing, target, link_role),
                        "program_version": _row["version"], "created": False}
            cur.execute("INSERT INTO design.program_object_link(program_id,object_kind,object_id,role,rationale,linked_by_kind,linked_by_id) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id,linked_at,linked_by_kind::text,linked_by_id,rationale",
                        (identifier, target["kind"], target["id"], link_role, rationale, who["kind"], who["id"]))
            link = self._link(cur.fetchone(), target, link_role)
            version = self._advance(cur, identifier, who)
            result = {"link": link, "program_version": version, "created": True}
            return self._finish(cur, identifier, version, "object.linked", target, result, who, request_id)

    def create_snapshot(self, program_ref: dict, expected_version: int,
                        actor: dict, request_id: str | None = None) -> dict:
        who = D.actor(actor)
        with self._mutation(program_ref, expected_version, request_id) as state:
            cur, identifier, _row, duplicate = state
            if duplicate is not None: return duplicate
            version = self._advance(cur, identifier, who)
            document = self._overview(cur, identifier); document["snapshot_version"] = version
            digest = D.digest(document); digest_bytes = bytes.fromhex(digest.removeprefix("sha256:"))
            cur.execute("INSERT INTO design.program_snapshot(program_id,aggregate_version,document,digest,created_by_kind,created_by_id) "
                        "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id,created_at",
                        (identifier, version, self._json(document), digest_bytes, who["kind"], who["id"]))
            snap = cur.fetchone()
            snapshot = {"ref": _ref("program_snapshot", snap["id"]), "program_ref": _ref("program", identifier),
                        "program_version": version, "digest": digest, "document": document,
                        "created_by": who, "created_at": snap["created_at"]}
            result = {"snapshot": snapshot}
            return self._finish(cur, identifier, version, "snapshot.created", snapshot["ref"], result, who, request_id)

    def _record_atom(self, program_ref: dict, expected_version: int, kind: str,
                     value: dict, actor: dict, request_id: str | None) -> dict:
        who = D.actor(actor)
        with self._mutation(program_ref, expected_version, request_id) as state:
            cur, identifier, _row, duplicate = state
            if duplicate is not None: return duplicate
            atom = getattr(self, f"_insert_{kind}")(cur, identifier, value, who)
            version = self._advance(cur, identifier, who)
            result = {kind: atom, "program_version": version}
            return self._finish(cur, identifier, version, f"{kind}.recorded", atom["ref"], result, who, request_id)

    def _insert_objective(self, cur, identifier, value, who):
        cur.execute("SELECT id,revision FROM design.program_objective WHERE program_id=%s AND objective_key=%s "
                    "ORDER BY revision DESC LIMIT 1 FOR UPDATE", (identifier, value["key"]))
        old = cur.fetchone(); revision = old["revision"] + 1 if old else 1
        if old: cur.execute("UPDATE design.program_objective SET status='superseded' WHERE id=%s AND status='active'", (old["id"],))
        cur.execute("INSERT INTO design.program_objective(program_id,objective_key,revision,title,rationale,category,metric,direction,threshold,priority,hardness,supersedes_id,created_by_kind,created_by_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,created_at",
                    (identifier, value["key"], revision, value["title"], value["rationale"], value["category"],
                     value["metric"], value["direction"], self._json(value["threshold"]), value["priority"],
                     value["hardness"], old["id"] if old else None, who["kind"], who["id"]))
        row = cur.fetchone(); return {"ref": _ref("objective", row["id"]), **value, "revision": revision,
                                      "supersedes_ref": _ref("objective", old["id"]) if old else None,
                                      "status": "active", "created_by": who, "created_at": row["created_at"]}

    def _insert_hypothesis(self, cur, identifier, value, who):
        cur.execute("SELECT id,revision FROM design.hypothesis WHERE program_id=%s AND hypothesis_key=%s "
                    "ORDER BY revision DESC LIMIT 1 FOR UPDATE", (identifier, value["key"]))
        old = cur.fetchone(); revision = old["revision"] + 1 if old else 1
        if old: cur.execute("UPDATE design.hypothesis SET status='superseded',updated_at=now() WHERE id=%s AND status='active'", (old["id"],))
        cur.execute("INSERT INTO design.hypothesis(program_id,hypothesis_key,revision,title,statement,falsification_criterion,confidence,supersedes_id,created_by_kind,created_by_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,created_at",
                    (identifier, value["key"], revision, value["title"], value["statement"],
                     value["falsification_criterion"], value["confidence"], old["id"] if old else None,
                     who["kind"], who["id"]))
        row = cur.fetchone(); return {"ref": _ref("hypothesis", row["id"]), **value, "revision": revision,
                                      "supersedes_ref": _ref("hypothesis", old["id"]) if old else None,
                                      "status": "active", "created_by": who, "created_at": row["created_at"]}

    def _insert_decision(self, cur, identifier, value, who):
        cur.execute("SELECT id,revision FROM design.decision WHERE program_id=%s AND decision_key=%s ORDER BY revision DESC LIMIT 1",
                    (identifier, value["key"]))
        old = cur.fetchone(); revision = old["revision"] + 1 if old else 1
        cur.execute("INSERT INTO design.decision(program_id,decision_key,revision,decision_type,action,outcome,rationale,alternatives,supersedes_id,decided_by_kind,decided_by_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,decided_at",
                    (identifier, value["key"], revision, value["type"], value["action"], value["outcome"],
                     value["rationale"], self._json(value["alternatives"]), old["id"] if old else None,
                     who["kind"], who["id"]))
        row = cur.fetchone(); return {"ref": _ref("decision", row["id"]), **value, "revision": revision,
                                      "supersedes_ref": _ref("decision", old["id"]) if old else None,
                                      "status": "recorded", "created_by": who, "created_at": row["decided_at"]}

    def _insert_milestone(self, cur, identifier, value, who):
        cur.execute("SELECT id,revision FROM design.program_milestone WHERE program_id=%s AND milestone_key=%s ORDER BY revision DESC LIMIT 1 FOR UPDATE",
                    (identifier, value["key"]))
        old = cur.fetchone(); revision = old["revision"] + 1 if old else 1
        if old: cur.execute("UPDATE design.program_milestone SET status='superseded' WHERE id=%s AND status IN ('planned','on_track','at_risk')", (old["id"],))
        cur.execute("INSERT INTO design.program_milestone(program_id,milestone_key,revision,title,description,target_date,criteria,supersedes_id,created_by_kind,created_by_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id,created_at",
                    (identifier, value["key"], revision, value["title"], value["description"], value["target_date"],
                     self._json(value["criteria"]), old["id"] if old else None, who["kind"], who["id"]))
        row = cur.fetchone(); return {"ref": _ref("milestone", row["id"]), **value, "revision": revision,
                                      "supersedes_ref": _ref("milestone", old["id"]) if old else None,
                                      "status": "planned", "created_by": who, "created_at": row["created_at"]}

    @contextmanager
    def _mutation(self, program_ref: dict, expected_version: int, request_id: str | None):
        identifier = D.ref(program_ref, "program")["id"]; expected = _version(expected_version)
        with self._connect() as conn, self._cursor(conn) as cur:
            cur.execute("SELECT * FROM design.project WHERE id=%s FOR UPDATE", (identifier,)); row = cur.fetchone()
            if row is None: raise failures.DiracNotFound("Program does not exist", details={"program_ref": program_ref})
            if request_id:
                cur.execute("SELECT payload->'result' AS result FROM design.program_event WHERE program_id=%s AND request_id=%s",
                            (identifier, request_id)); duplicate = cur.fetchone()
                if duplicate is not None:
                    yield (cur, identifier, row, duplicate["result"]); return
            if row["version"] != expected:
                raise failures.DiracInvalidParameters("Program version conflict",
                    details={"expected_version": expected, "current_version": row["version"]})
            yield (cur, identifier, row, None)

    def _advance(self, cur, identifier, who) -> int:
        cur.execute("UPDATE design.project SET version=version+1,updated_at=now(),updated_by_kind=%s,updated_by_id=%s "
                    "WHERE id=%s RETURNING version", (who["kind"], who["id"], identifier))
        return cur.fetchone()["version"]

    @staticmethod
    def _require_entity(cur, object_ref):
        cur.execute("SELECT canonical_key,label FROM app.entity WHERE kind=%s AND id=%s",
                    (object_ref["kind"], object_ref["id"]))
        if cur.fetchone() is None:
            raise failures.DiracNotFound("ObjectRef does not resolve to a canonical Dirac entity",
                details={"object_ref": object_ref})

    def _finish(self, cur, identifier, version, event_kind, atom_ref, result, who, request_id):
        event_id = self._insert_event(cur, identifier, version, event_kind, atom_ref, result, who, request_id)
        output = D.jsonable(result); output["event_ref"] = _event_ref(event_id)
        self._update_event_result(cur, event_id, output)
        return output

    def _insert_event(self, cur, identifier, version, kind, atom_ref, result, who, request_id):
        atom_kind = atom_ref["kind"] if atom_ref else None; atom_id = atom_ref["id"] if atom_ref else None
        cur.execute("INSERT INTO design.program_event(program_id,aggregate_version,event_kind,atom_kind,atom_id,payload,request_id,actor_kind,actor_id) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                    (identifier, version, kind, atom_kind, atom_id, self._json({"result": result}), request_id, who["kind"], who["id"]))
        return cur.fetchone()["id"]

    def _update_event_result(self, cur, event_id, result):
        cur.execute("UPDATE design.program_event SET payload=jsonb_set(payload,'{result}',%s::jsonb) WHERE id=%s",
                    (self._json(result), event_id))

    def _overview(self, cur, identifier):
        cur.execute("SELECT id,code::text,name,target_id,portfolio_id,lifecycle::text,stage::text,version,summary,indication,modality,owner_id,created_at,updated_at,archived_at,updated_by_kind::text,updated_by_id FROM design.project WHERE id=%s", (identifier,))
        row = cur.fetchone()
        if row is None: raise failures.DiracNotFound("Program does not exist", details={"program_ref": _ref("program", identifier)})
        out = self._program(row)
        queries = {
            "objectives": "SELECT id,objective_key::text AS key,revision,title,rationale,category,metric,direction,threshold,priority,hardness,status,supersedes_id,created_at,created_by_kind::text AS actor_kind,created_by_id AS actor_id FROM design.program_objective WHERE program_id=%s ORDER BY created_at DESC",
            "hypotheses": "SELECT id,hypothesis_key::text AS key,revision,title,statement,falsification_criterion,confidence,status,supersedes_id,created_at,created_by_kind::text AS actor_kind,created_by_id AS actor_id FROM design.hypothesis WHERE program_id=%s ORDER BY created_at DESC",
            "decisions": "SELECT id,decision_key::text AS key,revision,decision_type AS type,action,outcome,rationale,alternatives,supersedes_id,decided_at AS created_at,decided_by_kind::text AS actor_kind,decided_by_id AS actor_id FROM design.decision WHERE program_id=%s ORDER BY decided_at DESC",
            "milestones": "SELECT id,milestone_key::text AS key,revision,title,description,target_date,criteria,status,supersedes_id,created_at,created_by_kind::text AS actor_kind,created_by_id AS actor_id FROM design.program_milestone WHERE program_id=%s ORDER BY created_at DESC",
        }
        kind_map = {"objectives":"objective","hypotheses":"hypothesis","decisions":"decision","milestones":"milestone"}
        for collection, sql in queries.items():
            cur.execute(sql, (identifier,)); out[collection] = [self._atom(r, kind_map[collection]) for r in cur.fetchall()]
        cur.execute("SELECT id,principal_kind::text,principal_id,role::text,responsibility,assigned_at,assigned_by_kind::text,assigned_by_id FROM design.program_member WHERE program_id=%s AND retired_at IS NULL ORDER BY role,assigned_at", (identifier,))
        out["members"] = [self._member(r, _ref(r["principal_kind"], r["principal_id"]), r["role"]) for r in cur.fetchall()]
        cur.execute("SELECT id,gate_key::text AS key,revision,stage::text,title,criteria,status,evidence_summary,decision_id,target_date,assessed_at,supersedes_id,created_at,created_by_kind::text AS actor_kind,created_by_id AS actor_id FROM design.program_stage_gate WHERE program_id=%s ORDER BY created_at DESC", (identifier,))
        out["stage_gates"] = [self._stage_gate(r) for r in cur.fetchall()]
        cur.execute("SELECT id,work_item_id,work_key::text AS key,revision,title,description,status,priority,owner_kind::text,owner_id,due_on,deliverable_refs,supersedes_id,created_at,created_by_kind::text AS actor_kind,created_by_id AS actor_id FROM design.program_work_package WHERE program_id=%s ORDER BY created_at DESC", (identifier,))
        packages = cur.fetchall(); package_ids = [r["id"] for r in packages]
        dependencies: dict[str, list[dict]] = {str(item): [] for item in package_ids}
        if package_ids:
            cur.execute("SELECT work_package_id,depends_on_id FROM design.program_work_dependency WHERE work_package_id=ANY(%s)", (package_ids,))
            for dependency in cur.fetchall():
                dependencies[str(dependency["work_package_id"])].append(_ref("work_package", dependency["depends_on_id"]))
        out["work_packages"] = [self._work_package(r, dependencies[str(r["id"])]) for r in packages]
        cur.execute("SELECT item.id,item.work_key::text AS key,item.title,item.current_lane::text AS lane,item.current_package_id,item.created_at,item.created_by_kind::text AS actor_kind,item.created_by_id AS actor_id,package.status,package.priority,package.owner_kind::text,package.owner_id,package.due_on FROM design.program_work_item item LEFT JOIN design.program_work_package package ON package.id=item.current_package_id WHERE item.program_id=%s ORDER BY item.created_at,item.work_key", (identifier,))
        work_rows = cur.fetchall(); work_ids = [row["id"] for row in work_rows]
        work_dependencies: dict[str, list[dict]] = {str(item): [] for item in work_ids}
        work_transitions: dict[str, list[dict]] = {str(item): [] for item in work_ids}
        work_executions: dict[str, list[dict]] = {str(item): [] for item in work_ids}
        if work_ids:
            cur.execute("SELECT work_item_id,depends_on_work_item_id FROM design.program_work_item_dependency WHERE work_item_id=ANY(%s)", (work_ids,))
            for dependency in cur.fetchall():
                work_dependencies[str(dependency["work_item_id"])].append(_ref("work_item", dependency["depends_on_work_item_id"]))
            cur.execute("SELECT id,work_item_id,from_lane::text,to_lane::text,reason,transitioned_at,transitioned_by_kind::text,transitioned_by_id FROM design.program_work_transition WHERE work_item_id=ANY(%s) ORDER BY transitioned_at DESC", (work_ids,))
            for transition in cur.fetchall():
                work_transitions[str(transition["work_item_id"])].append(self._work_transition(transition))
            cur.execute("SELECT execution.id,execution.work_item_id,execution.job_id,execution.purpose,execution.linked_at,execution.linked_by_kind::text,execution.linked_by_id,job.state::text AS job_state FROM design.program_work_execution execution JOIN app.job job ON job.id=execution.job_id WHERE execution.work_item_id=ANY(%s) ORDER BY execution.linked_at DESC", (work_ids,))
            for execution in cur.fetchall():
                work_executions[str(execution["work_item_id"])].append(
                    {**self._work_execution(execution, _ref("job", execution["job_id"])),
                     "job_state": execution["job_state"]})
        packages_by_id = {package["ref"]["id"]: package for package in out["work_packages"]}
        out["work_items"] = [self._work_item(row,
            packages_by_id.get(str(row["current_package_id"])),
            work_dependencies[str(row["id"])], work_transitions[str(row["id"])],
            work_executions[str(row["id"])]) for row in work_rows]
        out["work_transitions"] = [item for work_item in out["work_items"] for item in work_item["transitions"]]
        out["work_executions"] = [item for work_item in out["work_items"] for item in work_item["executions"]]
        cur.execute("SELECT id,subject_kind::text,subject_id,relation::text,evidence_kind::text,evidence_id,claim,strength,attached_at,attached_by_kind::text,attached_by_id FROM design.program_evidence_binding WHERE program_id=%s ORDER BY attached_at DESC", (identifier,))
        out["evidence_bindings"] = [self._evidence(r, {"subject_ref": _ref(r["subject_kind"], r["subject_id"]),
            "evidence_ref": _ref(r["evidence_kind"], r["evidence_id"]), "relation": r["relation"]}) for r in cur.fetchall()]
        cur.execute("SELECT id,object_kind::text,object_id,role,rationale,linked_at,linked_by_kind::text,linked_by_id FROM design.program_object_link WHERE program_id=%s AND retired_at IS NULL ORDER BY linked_at DESC", (identifier,))
        out["links"] = [self._link(r, _ref(r["object_kind"], r["object_id"]), r["role"]) for r in cur.fetchall()]
        linked_pairs = {(link["object_ref"]["kind"], link["object_ref"]["id"]) for link in out["links"]}
        if linked_pairs:
            cur.execute("SELECT id,source_kind::text,source_id,relation::text,target_kind::text,target_id,created_at,actor_kind::text,actor_id FROM app.object_relation WHERE (source_kind::text,source_id) IN (SELECT * FROM unnest(%s::text[],%s::text[])) OR (target_kind::text,target_id) IN (SELECT * FROM unnest(%s::text[],%s::text[])) ORDER BY created_at DESC LIMIT 100",
                        ([pair[0] for pair in linked_pairs], [pair[1] for pair in linked_pairs],
                         [pair[0] for pair in linked_pairs], [pair[1] for pair in linked_pairs]))
            out["lineage"] = [{"ref": _ref("artifact", r["id"]), "source_ref": _ref(r["source_kind"], r["source_id"]),
                "relation": r["relation"], "target_ref": _ref(r["target_kind"], r["target_id"]),
                "created_at": r["created_at"], "created_by": {"kind": r["actor_kind"], "id": r["actor_id"]}}
                for r in cur.fetchall() if (r["source_kind"], r["relation"], r["target_kind"]) in D.LINEAGE_SHAPES]
        else:
            out["lineage"] = []
        cur.execute("SELECT id,event_kind,aggregate_version,atom_kind::text,atom_id,occurred_at,actor_kind::text,actor_id FROM design.program_event WHERE program_id=%s ORDER BY aggregate_version DESC LIMIT 30", (identifier,))
        out["events"] = [{"ref": _event_ref(r["id"]), "kind": r["event_kind"], "program_version": r["aggregate_version"],
                          "atom_ref": _ref(r["atom_kind"], r["atom_id"]) if r["atom_kind"] else None,
                          "occurred_at": r["occurred_at"], "actor": {"kind": r["actor_kind"], "id": r["actor_id"]}} for r in cur.fetchall()]
        out["counts"] = {name: len(out[name]) for name in ("objectives","hypotheses","decisions","milestones","links",
            "members","stage_gates","work_items","work_packages","work_transitions","work_executions",
            "evidence_bindings","lineage")}
        out["health"] = _program_health(out)
        return D.jsonable(out)

    @staticmethod
    def _program(row):
        actor = ({"kind": row.get("updated_by_kind"), "id": row.get("updated_by_id")}
                 if row.get("updated_by_kind") else None)
        return {"ref": _ref("program", row["id"]), "code": row["code"], "name": row["name"],
                "summary": row.get("summary"), "indication": row.get("indication"), "modality": row.get("modality"),
                "owner_id": row.get("owner_id"), "lifecycle": row["lifecycle"], "stage": row["stage"],
                "version": row["version"], "target_ref": _ref("target", row["target_id"]) if row.get("target_id") else None,
                "portfolio_ref": _ref("portfolio", row["portfolio_id"]) if row.get("portfolio_id") else None,
                "created_at": row.get("created_at"), "updated_at": row.get("updated_at"),
                "archived_at": row.get("archived_at"), "updated_by": actor}

    @classmethod
    def _summary(cls, row):
        return D.jsonable(cls._program(row))

    @staticmethod
    def _portfolio(row):
        return D.jsonable({"ref": _ref("portfolio", row["id"]), "code": row["code"], "name": row["name"],
            "mandate": row.get("mandate"), "lifecycle": row["lifecycle"], "version": row["version"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "updated_by": {"kind": row["updated_by_kind"], "id": row["updated_by_id"]}})

    @staticmethod
    def _atom(row, kind):
        data = dict(row); identifier = data.pop("id"); supersedes = data.pop("supersedes_id", None)
        actor_kind = data.pop("actor_kind"); actor_id = data.pop("actor_id")
        if kind == "hypothesis" and data.get("confidence") is not None: data["confidence"] = float(data["confidence"])
        return D.jsonable({"ref": _ref(kind, identifier), **data,
                           "supersedes_ref": _ref(kind, supersedes) if supersedes else None,
                           "created_by": {"kind": actor_kind, "id": actor_id}})

    @staticmethod
    def _link(row, target, role):
        return D.jsonable({"ref": _ref("artifact", row["id"]), "object_ref": target, "role": role,
                           "rationale": row.get("rationale"), "linked_at": row["linked_at"],
                           "linked_by": {"kind": row.get("linked_by_kind"), "id": row.get("linked_by_id")}})

    @staticmethod
    def _member(row, principal, role):
        return D.jsonable({"ref": _ref("artifact", row["id"]), "principal": principal, "role": role,
            "responsibility": row.get("responsibility"), "assigned_at": row["assigned_at"],
            "assigned_by": {"kind": row["assigned_by_kind"], "id": row["assigned_by_id"]}})

    @staticmethod
    def _stage_gate(row):
        data = dict(row); identifier = data.pop("id"); supersedes = data.pop("supersedes_id", None)
        actor_kind = data.pop("actor_kind"); actor_id = data.pop("actor_id"); decision_id = data.pop("decision_id", None)
        return D.jsonable({"ref": _ref("stage_gate", identifier), **data,
            "decision_ref": _ref("decision", decision_id) if decision_id else None,
            "supersedes_ref": _ref("stage_gate", supersedes) if supersedes else None,
            "created_by": {"kind": actor_kind, "id": actor_id}})

    @staticmethod
    def _work_package(row, dependencies):
        data = dict(row); identifier = data.pop("id"); supersedes = data.pop("supersedes_id", None)
        work_item_id = data.pop("work_item_id", None)
        actor_kind = data.pop("actor_kind"); actor_id = data.pop("actor_id")
        owner_kind = data.pop("owner_kind", None); owner_id = data.pop("owner_id", None)
        return D.jsonable({"ref": _ref("work_package", identifier),
            "work_item_ref": _ref("work_item", work_item_id) if work_item_id else None, **data,
            "owner": {"kind": owner_kind, "id": owner_id} if owner_kind else None,
            "depends_on_refs": dependencies,
            "supersedes_ref": _ref("work_package", supersedes) if supersedes else None,
            "created_by": {"kind": actor_kind, "id": actor_id}})

    @staticmethod
    def _work_item(row, package, dependencies, transitions, executions):
        return D.jsonable({"ref": _ref("work_item", row["id"]), "key": row["key"],
            "title": row["title"], "lane": row["lane"], "status": row.get("status") or "backlog",
            "priority": row.get("priority"),
            "owner": _ref(row["owner_kind"], row["owner_id"]) if row.get("owner_kind") else None,
            "due_on": row.get("due_on"), "current_package": package,
            "depends_on_refs": dependencies, "transitions": transitions, "executions": executions,
            "created_at": row["created_at"],
            "created_by": {"kind": row["actor_kind"], "id": row["actor_id"]}})

    @staticmethod
    def _work_transition(row):
        return D.jsonable({"ref": _ref("artifact", row["id"]),
            "work_item_ref": _ref("work_item", row["work_item_id"]),
            "from_lane": row["from_lane"], "to_lane": row["to_lane"], "reason": row["reason"],
            "transitioned_at": row["transitioned_at"],
            "transitioned_by": {"kind": row["transitioned_by_kind"], "id": row["transitioned_by_id"]}})

    @staticmethod
    def _work_execution(row, job_ref):
        return D.jsonable({"ref": _ref("artifact", row["id"]),
            "work_item_ref": _ref("work_item", row["work_item_id"]), "job_ref": job_ref,
            "purpose": row.get("purpose"), "linked_at": row["linked_at"],
            "linked_by": {"kind": row["linked_by_kind"], "id": row["linked_by_id"]}})

    @staticmethod
    def _evidence(row, item):
        strength = row.get("strength")
        return D.jsonable({"ref": _ref("artifact", row["id"]), **item, "claim": row["claim"],
            "strength": float(strength) if strength is not None else None,
            "attached_at": row["attached_at"],
            "attached_by": {"kind": row["attached_by_kind"], "id": row["attached_by_id"]}})
