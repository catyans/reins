"""Freeze real, commit-pinned project documents before any model experiment."""

import argparse
import concurrent.futures
import hashlib
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from reins.projects.documents import atomic_json, digest

CANDIDATES = """
encode/httpx psf/requests tiangolo/fastapi pydantic/pydantic encode/starlette
pallets/flask pallets/click Textualize/rich Textualize/textual celery/celery
pytest-dev/pytest pypa/pip pypa/hatch astral-sh/ruff astral-sh/uv
python-poetry/poetry python/black sphinx-doc/sphinx mkdocs/mkdocs
django/django tornado-web/tornado aio-libs/aiohttp scrapy/scrapy
scrapy/parsel psf/beautifulsoup4 arrow-py/arrow python-pendulum/pendulum
python-attrs/attrs python-attrs/cattrs marshmallow-code/marshmallow
sqlalchemy/sqlalchemy redis/redis-py rq/rq coleifer/peewee
tinydb/tinydb duckdb/duckdb narwhals-dev/narwhals pola-rs/polars
pandas-dev/pandas numpy/numpy scipy/scipy scikit-learn/scikit-learn
networkx/networkx sympy/sympy matplotlib/matplotlib mwaskom/seaborn
plotly/plotly.py bokeh/bokeh altair-viz/altair streamlit/streamlit
gradio-app/gradio plotly/dash holoviz/panel voila-dashboards/voila
ipython/ipython jupyter/notebook jupyterlab/jupyterlab dask/dask
joblib/joblib mlflow/mlflow bentoml/BentoML ray-project/ray
huggingface/transformers huggingface/datasets huggingface/tokenizers
UKPLab/sentence-transformers openai/openai-python anthropics/anthropic-sdk-python
googleapis/python-genai langchain-ai/langchain run-llama/llama_index
deepset-ai/haystack pydantic/pydantic-ai microsoft/autogen
PrefectHQ/prefect dagster-io/dagster apache/airflow apache/superset
great-expectations/great_expectations unionai-oss/pandera dbt-labs/dbt-core
litestar-org/litestar sanic-org/sanic falconry/falcon bottlepy/bottle
Delgan/loguru getlogbook/logbook hynek/structlog jd/tenacity
agronholm/anyio python-trio/trio agronholm/apscheduler dbader/schedule
beetbox/beets yt-dlp/yt-dlp python-babel/babel jaraco/keyring
python-jsonschema/jsonschema yaml/pyyaml uiri/toml python-toml/toml
""".split()


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Reins-project-pilot/1.0"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                raw = response.read(600_001)
            if len(raw) > 600_000:
                raise ValueError("Document exceeds 600 KB collection limit")
            return raw.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if attempt == 2:
                raise
        except (OSError, TimeoutError):
            if attempt == 2:
                raise
        time.sleep(attempt + 1)


def document(repo, sha, paths):
    for path in paths:
        url = f"https://raw.githubusercontent.com/{repo}/{sha}/{path}"
        content = fetch(url)
        if content and len(content.strip()) > 40:
            return {"url": url, "path": path, "text": content, "hash": digest(content)}
    return None


def collect(repo, out):
    cache = out / "sources" / (repo.replace("/", "__") + ".json")
    if cache.exists():
        return json.loads(cache.read_text())
    process = subprocess.run(
        [
            "git",
            "ls-remote",
            "--tags",
            "--sort=-version:refname",
            f"https://github.com/{repo}.git",
            "refs/tags/*",
        ],
        capture_output=True,
        text=True,
        timeout=75,
        check=True,
    )
    refs = dict(line.split("\t", 1)[::-1] for line in process.stdout.splitlines())
    tags = []
    for ref, sha in refs.items():
        name = ref.removeprefix("refs/tags/")
        if re.fullmatch(r"v?\d+\.\d+(?:\.\d+)?", name):
            tags.append((name, refs.get(ref + "^{}", sha)))
    # Numerical release order, never dependent on extraction results.
    tags.sort(key=lambda pair: tuple(map(int, pair[0].lstrip("v").split("."))), reverse=True)
    selected = []
    seen = set()
    # Spaced releases reduce collecting many identical patch-release README files.
    indices = list(dict.fromkeys([0, 3, 9, 18, 30, 50, 80, 120, 1, 2, 5, 14, 24]))
    for index in indices:
        if index >= len(tags):
            continue
        tag, sha = tags[index]
        readme = document(repo, sha, ["README.md", "README.rst", "README", "Readme.md"])
        if not readme or readme["hash"] in seen:
            continue
        seen.add(readme["hash"])
        docs = {"readme": readme}
        license_doc = document(repo, sha, ["LICENSE", "LICENSE.txt", "LICENSE.md", "COPYING"])
        if license_doc:
            docs["license"] = license_doc
        changes = document(repo, sha, ["CHANGELOG.md", "CHANGES.rst", "HISTORY.md"])
        if changes:
            docs["changes"] = changes
        selected.append({"version": tag, "commit": sha, "documents": docs})
        if len(selected) == 3:
            break
    if len(selected) != 3:
        raise ValueError("Fewer than three distinct readable README release snapshots")
    selected.sort(key=lambda row: tuple(map(int, row["version"].lstrip("v").split("."))))
    result = {"project": repo, "collected_at": time.time(), "snapshots": selected}
    atomic_json(cache, result)
    return result


def main(out, count=60):
    out = Path(out)
    manifest_path = out / "dataset.json"
    if manifest_path.exists():
        print("Frozen dataset already exists; not replacing it.", flush=True)
        return
    out.mkdir(parents=True, exist_ok=True)
    rows, failures = {}, {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        jobs = {pool.submit(collect, repo, out): repo for repo in CANDIDATES}
        for job in concurrent.futures.as_completed(jobs):
            repo = jobs[job]
            try:
                rows[repo] = job.result()
                print(json.dumps({"collected": len(rows), "project": repo}), flush=True)
            except Exception as exc:
                failures[repo] = type(exc).__name__ + ": " + str(exc)[:160]
                print(json.dumps({"skipped": repo, "reason": failures[repo]}), flush=True)
    ordered = sorted(rows, key=lambda repo: hashlib.sha256(repo.encode()).hexdigest())
    groups, chosen = {}, []
    limits = {split: count // 3 for split in ("development", "validation", "test")}
    for repo in ordered:
        owner = repo.split("/")[0].lower()
        split = groups.get(owner)
        if split is None:
            split = next((s for s, remaining in limits.items() if remaining), None)
        if split is None or not limits[split]:
            continue
        groups[owner] = split
        limits[split] -= 1
        chosen.append({**rows[repo], "split": split})
    atomic_json(out / "collection-log.json", {"failures": failures, "available": len(rows)})
    if len(chosen) != count:
        raise RuntimeError(
            f"Only {len(chosen)}/{count} eligible projects; no model experiment started"
        )
    manifest = {
        "schema_version": 1,
        "kind": "real_release_history_replay",
        "frozen_at": time.time(),
        "selection": "sha256(project), owner-isolated splits",
        "projects": chosen,
    }
    manifest["dataset_hash"] = digest(manifest)
    atomic_json(manifest_path, manifest)
    print(json.dumps({"frozen_projects": len(chosen), "dataset_hash": manifest["dataset_hash"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="output/benchmarks/projects-v2")
    args = parser.parse_args()
    main(args.out)
