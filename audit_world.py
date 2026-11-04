import datetime
import json
import os
import subprocess

import attrs
import cattrs
import packaging.version
import requests

converter = cattrs.Converter()

converter.register_structure_hook(datetime.datetime, lambda x, *_: datetime.datetime.fromisoformat(x) if x else None)
converter.register_unstructure_hook(datetime.datetime, lambda x, *_: x.isoformat() if x else None)

@attrs.define
class HistoryInfo:
    last_checked: datetime.datetime
    last_version: str

def run() -> None:

    if os.path.exists("history.json"):
        with open("history.json", "r") as f:
            history = converter.structure(json.load(f), dict[str, HistoryInfo])
    else:
        history = {}

    response = requests.get("https://raw.githubusercontent.com/silasary/apworlds/refs/heads/main/index.json")
    world_index = response.json()
    worlds = {}
    world = None
    world_id = None
    for world in world_index["worlds"]:
        world_id = world["metadata"].get("id")
        version = packaging.version.parse(world["metadata"].get("world_version", "0"))
        if world_id and (world_id not in worlds or version > packaging.version.parse(worlds[world_id]["metadata"].get("world_version", "0"))):
            worlds[world_id] = world

    def sort_key(item) -> datetime.datetime:
        return history.get(item[0], HistoryInfo(datetime.datetime.min, "0")).last_checked
    sorted_worlds = dict(sorted(worlds.items(), key=sort_key))

    for world_id, world in sorted_worlds.items():
        last_run = history.get(world_id, HistoryInfo(datetime.datetime.min, "0"))
        last_checked = last_run.last_checked
        last_version = last_run.last_version
        print(f"World ID: {world_id}, Version: {world['metadata'].get('world_version', '0')}, Last checked: {last_checked if last_checked != datetime.datetime.min else 'Never'}, Last version: {last_version}")
        if last_version != world['metadata'].get('world_version', '0'):
            break
    else:
        print("No world found with a new version.")
        return

    history[world_id] = HistoryInfo(datetime.datetime.now(), world['metadata'].get('world_version', '0'))
    dump = converter.unstructure(history)
    with open("history.json", "w") as f:
        json.dump(dump, f, indent=1)

    download_url = world["world"]
    response = requests.get(download_url)
    world_path = "worlds"
    out_path = "out"
    os.makedirs(world_path, exist_ok=True)
    os.makedirs(out_path, exist_ok=True)
    for file_name in os.listdir(world_path):
        if file_name.endswith(".apworld"):
            os.remove(os.path.join(world_path, file_name))

    with open(os.path.join(world_path, f"{world_id}.apworld"), "wb") as f:
        f.write(response.content)
    docker_cmd = [
        "docker", "run", "--rm",
        "-v", f"{os.path.abspath(world_path)}:/worlds",
        "-v", f"{os.path.abspath(out_path)}:/out",
        "ghcr.io/silasary/ap-lint:main",
    ]
    subprocess.run(docker_cmd, check=True)
    results_path = os.path.join("results", world_id)
    os.makedirs(results_path, exist_ok=True)
    for file_name in os.listdir(out_path):
        os.rename(os.path.join(out_path, file_name), os.path.join(results_path, file_name))
    pass

if __name__ == "__main__":
    run()
