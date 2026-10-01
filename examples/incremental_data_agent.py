"""Two sources, isolated checkpoints and selective refresh. No API calls or fees."""

import argparse
import asyncio
import json

from reins import CheckpointStore


async def main(path):
    store = CheckpointStore(path)
    try:
        for release in ["v1", "v1", "v2"]:
            # Replace each execute callback with your instrumented retrieval or LLM step.
            project = await store.run(
                isolation="demo-tenant",
                step="profile",
                version="extract-v1",
                inputs={"readme": "Example project"},
                execute=lambda: {"name": "Example"},
            )
            version = await store.run(
                isolation="demo-tenant",
                step="release",
                version="extract-v1",
                inputs={"release": release},
                execute=lambda: {"release": release},
            )
            combined = await store.run(
                isolation="demo-tenant",
                step="join",
                version="join-v1",
                inputs={},
                dependencies=[project["revision"], version["revision"]],
                execute=lambda: project["value"] | version["value"],
            )
            print(
                json.dumps(
                    {
                        "result": combined["value"],
                        "reused": {
                            "profile": project["reused"],
                            "release": version["reused"],
                            "join": combined["reused"],
                        },
                    }
                )
            )
    finally:
        store.close()


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--database", default="/tmp/reins-checkpoints.sqlite")
    asyncio.run(main(p.parse_args().database))
