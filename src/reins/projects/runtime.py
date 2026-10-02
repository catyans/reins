"""Single-writer project runner. Public status is a sanitized atomic projection."""

import asyncio
import contextlib
import fcntl
import json
import os
import sqlite3
import time
import urllib.request
from decimal import Decimal
from pathlib import Path

from reins import (
    CheckpointStore,
    UnsettledAttempt,
    record_external_cost,
    record_outcome,
    step,
)
from reins.checkpoints import fingerprint
from reins.core.context import get_current_run
from reins.core.decorators import trace
from reins.google_usage import GoogleTextPrices, estimate_google_text_cost
from reins.projects.documents import (
    FIELDS,
    all_sources,
    atomic_json,
    compact_sources,
    digest,
    field_sources,
    prompt,
    rules,
    supported,
)

MODELS = {"lite": "gemini-2.5-flash-lite", "flash": "gemini-2.5-flash"}
PRICES = {"lite": ("0.10", "0.40", "0.01"), "flash": ("0.30", "2.50", "0.03")}
POLICIES = ("parser", "fixed-lite", "fixed-flash", "cached-cascade", "incremental")
AVAILABLE_POLICIES = (*POLICIES, "grouped-incremental", "cached-flash")
EXTRACTOR_VERSION = "project-extract-v1"


class SourceUnavailable(ValueError):
    """Missing or changed pinned input; never overwrite the last valid result."""


class RejectedCheckpoint(ValueError):
    """A settled but unsupported extraction must not be reused as a valid result."""


def load_key(path=None):
    value = os.environ.get("GEMINI_API_KEY") or os.environ.get("DTA_GOOGLE_GENAI__API_KEY")
    if value:
        return value
    if path:
        for line in Path(path).read_text().splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() in ("GEMINI_API_KEY", "DTA_GOOGLE_GENAI__API_KEY"):
                return value.strip().strip("\"'")
    raise ValueError("Provide GEMINI_API_KEY or --key-file; credentials are never exported")


class State:
    def __init__(self, root, *, writer=True):
        self.root = Path(root)
        self.lock = None
        if writer:
            self.root.mkdir(parents=True, exist_ok=True)
            self.lock = (self.root / "worker.lock").open("a")
            try:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                self.lock.close()
                raise RuntimeError("Another project worker is already running") from None
            self.db = sqlite3.connect(self.root / "jobs.sqlite")
            self.db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS jobs(
                    id TEXT PRIMARY KEY, project TEXT, policy TEXT, phase TEXT, split TEXT,
                    state TEXT, updated REAL, result TEXT);
                CREATE TABLE IF NOT EXISTS calls(
                    id INTEGER PRIMARY KEY, job_id TEXT, model TEXT, purpose TEXT,
                    state TEXT, reserve REAL, cost REAL, seconds REAL, response TEXT,
                    request_key TEXT UNIQUE);
                CREATE TABLE IF NOT EXISTS events(
                    id INTEGER PRIMARY KEY, job_id TEXT, stage TEXT, time REAL);
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
            """)
            self.db.execute("UPDATE jobs SET state='interrupted' WHERE state='running'")
            self.db.execute("UPDATE calls SET state='uncertain' WHERE state='inflight'")
            self.db.commit()
        else:
            self.db = sqlite3.connect(f"file:{self.root / 'jobs.sqlite'}?mode=ro", uri=True)
        self.db.row_factory = sqlite3.Row
        self.activity = {}
        self.started = time.time()

    def close(self):
        self.db.close()
        if self.lock:
            fcntl.flock(self.lock, fcntl.LOCK_UN)
            self.lock.close()

    def acknowledge_circuit(self, reason):
        """Permit NEW work after operator inspection; never settle or retry old calls."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("An operator explanation is required")
        last_id = self.db.execute("SELECT coalesce(max(id),0) FROM calls").fetchone()[0]
        self.db.execute("INSERT OR REPLACE INTO settings VALUES ('circuit_ack',?)", (str(last_id),))
        self.db.execute(
            "INSERT INTO events(job_id,stage,time) VALUES (?,?,?)",
            ("operator", "Circuit acknowledged for new work: " + reason, time.time()),
        )
        self.db.commit()

    def resume_settled_jobs(self):
        self.db.execute(
            "UPDATE jobs SET state='queued' WHERE state IN ('needs_review','interrupted') "
            "AND id NOT IN (SELECT job_id FROM calls WHERE cost IS NULL)"
        )
        self.db.commit()

    def event(self, job, stage, **info):
        self.activity[job] = {
            **self.activity.get(job, {}),
            "id": job,
            "stage": stage,
            "updated_at": time.time(),
            **info,
        }
        self.db.execute(
            "INSERT INTO events(job_id,stage,time) VALUES (?,?,?)", (job, stage, time.time())
        )
        self.db.commit()

    def export(self, dest):
        jobs = [
            dict(r)
            for r in self.db.execute(
                "SELECT id,project,policy,phase,split,state,updated,result "
                "FROM jobs ORDER BY updated DESC"
            )
        ]
        calls = self.db.execute(
            "SELECT count(*) n,coalesce(sum(cost),0) cost,"
            "sum(CASE WHEN cost IS NULL THEN 1 ELSE 0 END) pending,"
            "sum(CASE WHEN state='inflight' THEN 1 ELSE 0 END) inflight FROM calls"
        ).fetchone()
        completed = [json.loads(j["result"]) for j in jobs if j["result"]]
        # Explicit allowlist. No prompts, exception bodies, credentials or arbitrary metadata.
        records = []
        for row in completed[:40]:
            records.append(
                {
                    k: row[k]
                    for k in (
                        "project",
                        "version",
                        "phase",
                        "policy",
                        "fields",
                        "reused",
                        "calls",
                        "cost",
                        "seconds",
                        "source_backed",
                        "sources",
                        "changed_fields",
                    )
                }
            )
        public = {
            "schema_version": 1,
            "updated_at": time.time(),
            "kind": "historical_replay",
            "state": (
                "running"
                if self.activity
                else "completed"
                if jobs and all(j["state"] == "completed" for j in jobs)
                else "idle"
            ),
            "progress": {
                "total": len(jobs),
                "completed": sum(j["state"] == "completed" for j in jobs),
                "needs_review": sum(j["state"] == "needs_review" for j in jobs),
            },
            "metrics": {
                "calls": calls["n"],
                "estimated_api_cost_usd": calls["cost"],
                "unsettled_calls": calls["pending"] or 0,
                "inflight_calls": calls["inflight"] or 0,
                "reused_fields": sum(r["reused"] for r in completed),
            },
            "activity": list(self.activity.values()),
            "records": records,
        }
        atomic_json(dest, public)
        return public


class Gemini:
    def __init__(self, state, key, *, limit=30.0, control=None):
        self.state, self.key, self.limit = state, key, limit
        self.control = control
        self.semaphore = asyncio.Semaphore(4)

    async def call(self, text, model, job_id, purpose="extraction"):
        prices = GoogleTextPrices(*(Decimal(v) for v in PRICES[model]))
        reserve = float(
            (
                Decimal(len(text.encode()) + 2048) * prices.input_per_million
                + Decimal(4096) * prices.output_per_million
            )
            / Decimal(1_000_000)
        )
        async with self.semaphore:
            request_key = digest([job_id, model, purpose, text])
            old = self.state.db.execute(
                "SELECT * FROM calls WHERE request_key=?", (request_key,)
            ).fetchone()
            if old:
                if old["state"] != "completed":
                    raise UnsettledAttempt(f"Provider call {old['id']} is unresolved")
                try:
                    return json.loads(
                        json.loads(old["response"])["choices"][0]["message"]["content"]
                    )
                except (TypeError, ValueError):
                    return None
            spent = self.state.db.execute(
                "SELECT coalesce(sum(coalesce(cost,reserve)),0) FROM calls"
            ).fetchone()[0]
            if spent + reserve > self.limit:
                raise RuntimeError("Experiment budget reached; existing results preserved")
            ack = self.state.db.execute(
                "SELECT value FROM settings WHERE key='circuit_ack'"
            ).fetchone()
            last_success = self.state.db.execute(
                "SELECT coalesce(max(id),0) FROM calls WHERE state='completed'"
            ).fetchone()[0]
            failures = self.state.db.execute(
                "SELECT count(*) FROM calls WHERE state='uncertain' AND id>?",
                (max(int(ack[0]) if ack else 0, last_success),),
            ).fetchone()[0]
            if failures >= 3:
                raise UnsettledAttempt(
                    "Three unresolved provider calls; reconcile before continuing"
                )
            cursor = self.state.db.execute(
                "INSERT INTO calls(job_id,model,purpose,state,reserve,request_key) "
                "VALUES (?,?,?,'inflight',?,?)",
                (job_id, MODELS[model], purpose, reserve, request_key),
            )
            call_id = cursor.lastrowid
            self.state.db.commit()
            request_body = {
                "model": MODELS[model],
                "messages": [{"role": "user", "content": text}],
                "temperature": 0,
                "max_tokens": 4096,
                "response_format": {"type": "json_object"},
                "extra_body": {"google": {"thinking_config": {"thinking_budget": 0}}},
            }
            started = time.perf_counter()

            def request():
                req = urllib.request.Request(
                    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                    data=json.dumps(request_body).encode(),
                    headers={
                        "Authorization": "Bearer " + self.key,
                        "Content-Type": "application/json",
                    },
                )
                with urllib.request.urlopen(req, timeout=90) as response:
                    return json.load(response)

            try:
                from reins.control.client import active_workflow
                from reins.control.usage import estimate_components

                controlled = self.control or active_workflow()
                usage_record = {}

                async def measured_request():
                    result = await asyncio.to_thread(request)
                    counts = result.get("usage")
                    measured = estimate_google_text_cost(counts, prices)
                    usage_record.update(
                        input_tokens=counts["prompt_tokens"],
                        output_tokens=counts["completion_tokens"],
                    )
                    return result, str(measured)

                if controlled:
                    response = await controlled.acall(
                        measured_request,
                        model="google/" + MODELS[model],
                        category="evaluation" if purpose != "extraction" else "model",
                        max_cost=str(Decimal(str(reserve)).quantize(Decimal("0.000000001"))),
                        operation_inputs={"job": job_id, "purpose": purpose, "text": text},
                        usage=usage_record,
                    )
                    # Component estimates are diagnostic only, separate from billed totals.
                    with contextlib.suppress(Exception):
                        controlled.client.post(
                            "context/sample",
                            {
                                "task_id": controlled.context["task_id"],
                                "sample_id": digest([str(self.state.root.resolve()), call_id]),
                                "revision": EXTRACTOR_VERSION,
                                "components": estimate_components(user=text),
                                "provider_input_tokens": usage_record.get("input_tokens"),
                            },
                        )
                else:
                    response, _ = await measured_request()
                cost = float(estimate_google_text_cost(response.get("usage"), prices))
                seconds = time.perf_counter() - started
                content = response["choices"][0]["message"]["content"]
                try:
                    answer = json.loads(content)
                except (TypeError, ValueError):
                    answer = None
                self.state.db.execute(
                    "UPDATE calls SET state='completed',cost=?,seconds=?,response=? WHERE id=?",
                    (cost, seconds, json.dumps(response), call_id),
                )
                self.state.db.commit()
                atomic_json(
                    self.state.root / "calls" / f"{call_id:06d}.json",
                    {
                        "id": call_id,
                        "job_id": job_id,
                        "purpose": purpose,
                        "model": MODELS[model],
                        "request": request_body,
                        "response": response,
                        "estimated_cost": cost,
                        "seconds": seconds,
                        "price_per_million": PRICES[model],
                    },
                )
                if purpose == "extraction" and get_current_run() is not None:
                    record_external_cost(cost, label="Gemini text usage estimate")
                return answer
            except BaseException as exc:
                from reins.control import ControlDenied

                if isinstance(exc, ControlDenied):
                    self.state.db.execute(
                        "DELETE FROM calls WHERE id=? AND state='inflight'", (call_id,)
                    )
                    self.state.db.commit()
                    raise
                self.state.db.execute(
                    "UPDATE calls SET state='uncertain',seconds=?,response=? "
                    "WHERE id=? AND state='inflight'",
                    (
                        time.perf_counter() - started,
                        json.dumps(
                            {
                                "error_type": type(exc).__name__,
                                "http_status": getattr(exc, "code", None),
                            }
                        ),
                        call_id,
                    ),
                )
                self.state.db.commit()
                # Never persist provider exception text (may contain sensitive request context).
                if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                    raise
                raise UnsettledAttempt(f"Provider call {call_id}: {type(exc).__name__}") from None


def valid_field(field, answer, sources):
    return supported(answer, sources) and (
        answer["value"] is None
        or field == "license"
        or answer["value"] in sources[answer["source"]]
    )


class Runner:
    def __init__(self, state, caller, public_path):
        self.state, self.caller, self.public_path = state, caller, Path(public_path)
        self.checkpoints = CheckpointStore(state.root / "checkpoints.sqlite")
        self.dataset_hash = None

    def job_id(self, project, snapshot, policy, phase):
        return digest(
            [self.dataset_hash, EXTRACTOR_VERSION, project, snapshot["commit"], policy, phase]
        )[:24]

    async def checkpoint(self, *, project, policy, **kwargs):
        """Recover local interrupted steps only when the durable request log is settled.

        Calls themselves are idempotent by request key. This is safe for this runner's
        read-only extraction and local writes, not a generic external-action retry.
        """
        key = fingerprint(
            [
                kwargs["isolation"],
                kwargs["step"],
                kwargs["version"],
                kwargs["inputs"],
                list(kwargs.get("dependencies", ())),
            ]
        )
        row = self.checkpoints.conn.execute(
            "SELECT status FROM checkpoints WHERE key=?", (key,)
        ).fetchone()
        if row and row[0] == "rejected":
            raise RejectedCheckpoint("Prior extraction did not pass source validation")
        if row and row[0] in ("running", "unsettled"):
            pending = self.state.db.execute(
                "SELECT count(*) FROM calls WHERE cost IS NULL AND job_id IN "
                "(SELECT id FROM jobs WHERE project=? AND policy=?)",
                (project, policy),
            ).fetchone()[0]
            if not pending:
                self.checkpoints.authorize_retry(
                    key,
                    reconciliation_reference="durable-request-log:all-settled:" + key,
                )
        result = await self.checkpoints.run(**kwargs)
        fields = kwargs["inputs"].get("fields", [kwargs["step"]])
        values = (
            result["value"] if "fields" in kwargs["inputs"] else {kwargs["step"]: result["value"]}
        )
        valid = isinstance(values, dict) and all(
            valid_field(field, values.get(field), kwargs["inputs"]["sources"]) for field in fields
        )
        if not valid:
            self.checkpoints.conn.execute(
                "UPDATE checkpoints SET status='rejected',error='source_validation' WHERE key=?",
                (key,),
            )
            self.checkpoints.conn.commit()
            if result["reused"]:
                raise RejectedCheckpoint("Unsupported saved output cannot be reused")
        return result

    async def extract(self, fields, sources, model, job_id, *, cascade, repair_sources=None):
        self.state.event(job_id, "Extract", model=MODELS[model])
        async with step("Extract project fields", kind="tool"):
            answer = await self.caller.call(prompt(fields, sources), model, job_id)
        answer = answer if isinstance(answer, dict) else {}
        bad = [
            field
            for field in fields
            if not valid_field(field, answer.get(field), sources)
            or (sources and answer.get(field, {}).get("value") is None)
        ]
        if bad and cascade:
            self.state.event(job_id, "Validate and repair", model=MODELS["flash"])
            async with step("Repair unsupported fields", kind="tool"):
                fixed = await self.caller.call(
                    prompt(bad, repair_sources or sources), "flash", job_id
                )
            if isinstance(fixed, dict):
                answer.update({field: fixed.get(field) for field in bad})
        return {field: answer.get(field) for field in fields}

    async def grouped_extract(self, project, snapshot, views, job_id):
        """Reuse only source-valid fields with identical full document dependencies.

        Missing or changed fields share one Flash request. Keep the old policies
        intact so measured comparisons and existing resumptions remain reproducible.
        """
        previous = self.state.db.execute(
            "SELECT result FROM jobs WHERE project=? AND policy='grouped-incremental' "
            "AND state='completed' AND id!=? ORDER BY updated DESC LIMIT 1",
            (project, job_id),
        ).fetchone()
        old = json.loads(previous[0]) if previous else {}
        answers, reused = {}, 0
        for field in FIELDS:
            deps = {name: digest(text) for name, text in views[field].items()}
            value = old.get("fields", {}).get(field)
            if old.get("dependencies", {}).get(field) == deps and valid_field(
                field, value, views[field]
            ):
                answers[field] = value
                reused += 1
        missing = [f for f in FIELDS if f not in answers]
        if missing:
            sources = {k: v for f in missing for k, v in views[f].items()}
            # One request, no speculative field-level calls or automatic repair.
            answers.update(await self.extract(missing, sources, "flash", job_id, cascade=False))
        return answers, reused

    @trace(agent_name="Public project update", task_type="project-update-v2")
    async def execute(self, project, snapshot, policy, phase, split, job_id):
        started = time.perf_counter()
        self.state.event(job_id, "Load snapshot", project=project, policy=policy, phase=phase)
        async with step("Verify pinned source snapshot", kind="retrieval"):
            documents = snapshot.get("documents", {})
            if not documents.get("readme", {}).get("text"):
                raise SourceUnavailable("The pinned README is unavailable")
            for document in documents.values():
                if not isinstance(document.get("text"), str):
                    raise SourceUnavailable("Unreadable source document")
                if digest(document["text"]) != document.get("hash"):
                    raise SourceUnavailable("Source content does not match its pinned hash")
        views = field_sources(snapshot)
        answers, reused = {}, 0
        self.state.event(job_id, "Detect changes", project=project, policy=policy, phase=phase)
        async with step("Check source dependencies", kind="retrieval"):
            if policy == "grouped-incremental":
                answers, reused = await self.grouped_extract(project, snapshot, views, job_id)
            elif policy == "parser":
                answers = {f: rules(f, views[f]) for f in FIELDS}
            elif policy in ("fixed-lite", "fixed-flash", "cached-cascade", "cached-flash"):
                fields = list(FIELDS)
                if policy == "cached-cascade":
                    answers = {f: rules(f, views[f]) for f in FIELDS}
                    fields = [f for f in FIELDS if answers[f] is None]
                sources = all_sources(snapshot)

                async def full():
                    return await self.extract(
                        fields,
                        sources,
                        "flash" if policy in ("fixed-flash", "cached-flash") else "lite",
                        job_id,
                        cascade=policy == "cached-cascade",
                    )

                if fields:
                    if policy in ("cached-cascade", "cached-flash"):
                        result = await self.checkpoint(
                            project=project,
                            policy=policy,
                            isolation=project + ":" + policy,
                            step="whole-extraction",
                            version=EXTRACTOR_VERSION,
                            inputs={"fields": fields, "sources": sources},
                            execute=full,
                        )
                        answers.update(result["value"])
                        reused = len(fields) if result["reused"] else 0
                    else:
                        answers.update(await full())
            else:
                for field in FIELDS:
                    source = views[field]
                    parsed = rules(field, source)

                    async def extract_field(field=field, source=source, parsed=parsed):
                        if parsed is not None:
                            return parsed
                        return (
                            await self.extract(
                                [field],
                                compact_sources(field, source),
                                "lite",
                                job_id,
                                cascade=True,
                                repair_sources=source,
                            )
                        )[field]

                    result = await self.checkpoint(
                        project=project,
                        policy=policy,
                        isolation=project + ":" + policy,
                        step=field,
                        version=EXTRACTOR_VERSION,
                        inputs={"sources": source},
                        execute=extract_field,
                    )
                    answers[field] = result["value"]
                    reused += int(result["reused"])
        self.state.event(job_id, "Validate", project=project, policy=policy, phase=phase)
        source_backed = all(valid_field(f, answers.get(f), views[f]) for f in FIELDS)
        # A source-backed null is not proof of semantic completeness. Separate judge below.
        record_outcome(
            success=source_backed, reason="Source support only; semantic evaluation separate"
        )
        costs = self.state.db.execute(
            "SELECT count(*) n,coalesce(sum(cost),0) cost,sum(cost IS NULL) pending "
            "FROM calls WHERE job_id=?",
            (job_id,),
        ).fetchone()
        previous = self.state.db.execute(
            "SELECT result FROM jobs WHERE project=? AND policy=? AND state='completed' "
            "AND id!=? ORDER BY updated DESC LIMIT 1",
            (project, policy, job_id),
        ).fetchone()
        old_fields = json.loads(previous[0])["fields"] if previous else {}
        result = {
            "project": project,
            "version": snapshot["version"],
            "commit": snapshot["commit"],
            "policy": policy,
            "phase": phase,
            "split": split,
            "fields": answers,
            "reused": reused,
            "calls": costs["n"],
            "cost": costs["cost"],
            "cost_complete": not costs["pending"],
            "seconds": time.perf_counter() - started,
            "source_backed": source_backed,
            "dependencies": {
                f: {name: digest(text) for name, text in views[f].items()} for f in FIELDS
            },
            "changed_fields": [f for f in FIELDS if answers.get(f) != old_fields.get(f)],
            "sources": {
                k: {"url": v["url"], "hash": v["hash"]} for k, v in snapshot["documents"].items()
            },
        }
        self.state.event(job_id, "Save", project=project, policy=policy, phase=phase)
        self.state.db.execute(
            "UPDATE jobs SET state='completed',result=?,updated=? WHERE id=?",
            (json.dumps(result), time.time(), job_id),
        )
        self.state.db.commit()
        atomic_json(self.state.root / "records" / (job_id + ".json"), result)
        return result

    async def run(self, dataset, *, splits=None, policies=POLICIES):
        self.dataset_hash = dataset["dataset_hash"]
        rows = [p for p in dataset["projects"] if splits is None or p["split"] in splits]
        for project in rows:
            snapshots = project["snapshots"] + [project["snapshots"][-1]]
            for phase, snapshot in zip(("initial", "update-1", "update-2", "unchanged"), snapshots):
                for policy in policies:
                    job = self.job_id(project["project"], snapshot, policy, phase)
                    self.state.db.execute(
                        "INSERT OR IGNORE INTO jobs VALUES (?,?,?,?,?,'queued',?,NULL)",
                        (job, project["project"], policy, phase, project["split"], time.time()),
                    )
        self.state.db.commit()

        async def heartbeat():
            while True:
                self.state.export(self.public_path)
                await asyncio.sleep(2)

        pulse = asyncio.create_task(heartbeat())
        semaphore = asyncio.Semaphore(4)

        async def project_job(project):
            async with semaphore:
                snapshots = project["snapshots"] + [project["snapshots"][-1]]
                for phase, snapshot in zip(
                    ("initial", "update-1", "update-2", "unchanged"), snapshots
                ):
                    # Rotate policies deterministically to reduce provider-order confounding.
                    order = list(policies)
                    rotation = int(digest([project["project"], phase])[:8], 16) % len(order)
                    for policy in order[rotation:] + order[:rotation]:
                        job = self.job_id(project["project"], snapshot, policy, phase)
                        status = self.state.db.execute(
                            "SELECT state FROM jobs WHERE id=?", (job,)
                        ).fetchone()[0]
                        if status in ("completed", "needs_review"):
                            continue
                        # A non-checkpoint baseline call can also be uncertain after a crash.
                        unresolved = self.state.db.execute(
                            "SELECT count(*) FROM calls WHERE job_id=? AND cost IS NULL",
                            (job,),
                        ).fetchone()[0]
                        if unresolved:
                            self.state.db.execute(
                                "UPDATE jobs SET state='needs_review' WHERE id=?", (job,)
                            )
                            self.state.db.commit()
                            continue
                        self.state.db.execute(
                            "UPDATE jobs SET state='running',updated=? WHERE id=?",
                            (time.time(), job),
                        )
                        self.state.db.commit()
                        try:
                            await self.execute(
                                project["project"], snapshot, policy, phase, project["split"], job
                            )
                        except (UnsettledAttempt, SourceUnavailable, RejectedCheckpoint):
                            self.state.db.execute(
                                "UPDATE jobs SET state='needs_review',updated=? WHERE id=?",
                                (time.time(), job),
                            )
                            self.state.db.commit()
                        finally:
                            self.state.activity.pop(job, None)
                    print(
                        json.dumps(
                            {
                                "project": project["project"],
                                "phase": phase,
                                "split": project["split"],
                            }
                        ),
                        flush=True,
                    )

        tasks = [asyncio.create_task(project_job(project)) for project in rows]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        finally:
            pulse.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pulse
            self.state.export(self.public_path)

    def close(self):
        self.checkpoints.close()
