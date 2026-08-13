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


class MemoryProgramRepository:
    """Semantically faithful process-local implementation for focused tests."""

    kind = "memory"
    durability = "process"

    def __init__(self) -> None:
        self.programs: dict[str, dict[str, Any]] = {}
        self.by_code: dict[str, str] = {}
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
            "milestones": [], "links": [],
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
                if k not in {"id", "objectives", "hypotheses", "decisions", "milestones", "links"}}
        base.update({k: copy.deepcopy(row[k]) for k in
                     ("objectives", "hypotheses", "decisions", "milestones", "links")})
        base["counts"] = {k: len(row[k]) for k in
                          ("objectives", "hypotheses", "decisions", "milestones", "links")}
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
            archived_at = datetime.now(timezone.utc) if spec["lifecycle"] == "archived" else None
            cur.execute(
                "INSERT INTO design.project(code,name,target_id,lifecycle,stage,summary,indication,modality,owner_id,"
                "archived_at,updated_by_kind,updated_by_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "RETURNING id",
                (spec["code"], spec["name"], target_id, spec["lifecycle"], spec["stage"],
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
                "SELECT id,code::text,name,summary,lifecycle::text,stage::text,version,target_id,owner_id,updated_at "
                f"FROM design.project {where} ORDER BY updated_at DESC,code LIMIT %s", args)
            return {"programs": [self._summary(row) for row in cur.fetchall()]}

    def update(self, program_ref: dict, expected_version: int, patch: dict,
               actor: dict, request_id: str | None = None) -> dict:
        who = D.actor(actor)
        with self._mutation(program_ref, expected_version, request_id) as state:
            cur, identifier, row, duplicate = state
            if duplicate is not None: return duplicate
            changes = D.update_patch(self._program(row), patch)
            assignments = []; values = []
            for field, value in changes.items():
                column = "target_id" if field == "target_ref" else field
                if field == "target_ref": value = value["id"] if value else None
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

    def link(self, program_ref: dict, expected_version: int, object_ref: dict, role: str,
             rationale: str | None, actor: dict, request_id: str | None = None) -> dict:
        target = D.ref(object_ref); link_role = D.key(role, "role"); who = D.actor(actor)
        if target["kind"] == "program":
            raise failures.DiracInvalidParameters("a Program cannot link another Program as a child object")
        with self._mutation(program_ref, expected_version, request_id) as state:
            cur, identifier, _row, duplicate = state
            if duplicate is not None: return duplicate
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
        cur.execute("SELECT id,code::text,name,target_id,lifecycle::text,stage::text,version,summary,indication,modality,owner_id,created_at,updated_at,archived_at,updated_by_kind::text,updated_by_id FROM design.project WHERE id=%s", (identifier,))
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
        cur.execute("SELECT id,object_kind::text,object_id,role,rationale,linked_at,linked_by_kind::text,linked_by_id FROM design.program_object_link WHERE program_id=%s AND retired_at IS NULL ORDER BY linked_at DESC", (identifier,))
        out["links"] = [self._link(r, _ref(r["object_kind"], r["object_id"]), r["role"]) for r in cur.fetchall()]
        cur.execute("SELECT id,event_kind,aggregate_version,atom_kind::text,atom_id,occurred_at,actor_kind::text,actor_id FROM design.program_event WHERE program_id=%s ORDER BY aggregate_version DESC LIMIT 30", (identifier,))
        out["events"] = [{"ref": _event_ref(r["id"]), "kind": r["event_kind"], "program_version": r["aggregate_version"],
                          "atom_ref": _ref(r["atom_kind"], r["atom_id"]) if r["atom_kind"] else None,
                          "occurred_at": r["occurred_at"], "actor": {"kind": r["actor_kind"], "id": r["actor_id"]}} for r in cur.fetchall()]
        out["counts"] = {name: len(out[name]) for name in ("objectives","hypotheses","decisions","milestones","links")}
        return D.jsonable(out)

    @staticmethod
    def _program(row):
        actor = ({"kind": row.get("updated_by_kind"), "id": row.get("updated_by_id")}
                 if row.get("updated_by_kind") else None)
        return {"ref": _ref("program", row["id"]), "code": row["code"], "name": row["name"],
                "summary": row.get("summary"), "indication": row.get("indication"), "modality": row.get("modality"),
                "owner_id": row.get("owner_id"), "lifecycle": row["lifecycle"], "stage": row["stage"],
                "version": row["version"], "target_ref": _ref("target", row["target_id"]) if row.get("target_id") else None,
                "created_at": row.get("created_at"), "updated_at": row.get("updated_at"),
                "archived_at": row.get("archived_at"), "updated_by": actor}

    @classmethod
    def _summary(cls, row):
        return D.jsonable(cls._program(row))

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
