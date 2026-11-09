import datetime
import json
import os
import subprocess
from json.decoder import JSONDecodeError

import attrs
import cattrs
import junitparser
import packaging.version
import requests

converter = cattrs.Converter()

converter.register_structure_hook(datetime.datetime, lambda x, *_: datetime.datetime.fromisoformat(x) if x else None)
converter.register_unstructure_hook(datetime.datetime, lambda x, *_: x.isoformat() if x else None)

@attrs.define
class HistoryInfo:
    last_checked: datetime.datetime
    last_version: str

def choose_world():
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
        return None, None

    history[world_id] = HistoryInfo(datetime.datetime.now(), world['metadata'].get('world_version', '0'))
    history = dict(sorted(history.items(), key=lambda item: item[0]))
    dump = converter.unstructure(history)
    with open("history.json", "w") as f:
        json.dump(dump, f, indent=1)
    return world, world_id

def run_checks(world, world_id, results_path):
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
    subprocess.run(docker_cmd, check=False)

    for file_name in os.listdir(out_path):
        os.replace(os.path.join(out_path, file_name), os.path.join(results_path, file_name))

def load_severity_data() -> dict[str, dict[str, int]]:
    severity: dict[str, dict[str, int]] = {}
    if os.path.exists("severity.json"):
        with open("severity.json", "r") as f:
            severity = json.load(f)
    return severity

def count_codes(severity: dict[str, dict[str, int]]) -> int:
    count = 0
    for category in severity.values():
        count += len(category)
    return count

def finalise_report(results_path) -> None:
    severity = load_severity_data()
    num_codes = count_codes(severity)
    penalties = {}

    score_ruff(results_path, severity.setdefault("ruff", {}), penalties)
    score_tests(results_path, severity.setdefault("tests", {}), penalties)
    score_manifest(results_path, severity.setdefault("manifest", {}), penalties)
    score_fuzzer(results_path, severity.setdefault("fuzzer", {}), penalties)

    score = 100 - sum(penalties.values())
    report = f"Final score: {score}\n"
    for name, penalty in sorted(penalties.items(), key=lambda item: item[1], reverse=True):
        report += f"\n{name}: {penalty}"
    print(report)
    with open(os.path.join(results_path, "report.txt"), "w") as f:
        f.write(report)
        f.write("\n")

    if count_codes(severity) > num_codes:
        with open("severity.json", "w") as f:
            json.dump(severity, f, indent=1, sort_keys=True)
            f.write("\n")

def score_tests(results_path, severity, penalties):
    test_results = junitparser.JUnitXml.fromfile(os.path.join(results_path, "report.xml"))
    for suite in test_results:
        for test in suite:
            name = test.name
            is_failure = test.is_failure or test.is_error
            if is_failure:
                penalty = severity.setdefault(name, -1)
                if penalty == -1:
                    penalty = 1
                if test.is_error:
                    penalty *= 2
                penalties[name] = penalties.get(name, 0) + penalty

def score_ruff(results_path, severity, penalties):
    try:
        with open(os.path.join(results_path, "ruff_stats.json"), "r") as f:
            ruff_stats = json.load(f)
    except JSONDecodeError:
        print("Error decoding ruff_stats.json, the world probably didn't load.")
        ruff_stats = []
        penalties["ruff_stats.json failed to load"] = 50

    for issue in ruff_stats:
        name = issue.get("name")
        penalty = severity.setdefault(name, -1)
        if penalty == -1:
            penalty = 1
        count = min(5, issue.get("count", 1))  # only ding them up to 5 times for the same issue
        penalty = penalty * count
        penalties[name] = penalties.get(name, 0) + penalty

def score_manifest(results_path, severity, penalties):
    def penalise(key) -> None:
        penalty = severity.setdefault(key, -1)
        if penalty == -1:
            penalty = 1
        penalties[key] = penalties.get(key, 0) + penalty

    with open(os.path.join(results_path, "stat.json"), "r") as f:
        stat = json.load(f)

    manifest = stat.get("manifest", {})
    if not manifest:
        penalise("archipelago.json is missing")
        return

    if manifest.get("version", 0) > 7:
        penalise("manifest version is higher than latest defined version")
    if manifest.get("compatible_version", 0) > 7:
        penalise("manifest compatible_version is higher than latest defined version")

def score_fuzzer(results_path, severity, penalties):
    if not os.path.exists(os.path.join(results_path, "fuzz_output.json")):
        penalties["fuzzer failed to run"] = 50
        return
    with open(os.path.join(results_path, "fuzz_output.json"), "r") as f:
        fuzz_output = json.load(f)

    errors = fuzz_output.get("errors", {})
    for _world, world_errors in errors.items():
        for error, occurrences in world_errors.items():
            error_name = error.split("\n")[0]
            penalty = severity.setdefault(error_name, -1)
            if penalty == -1:
                penalty = 1
            penalty = penalty * len(occurrences)
            penalties[error_name] = penalties.get(error_name, 0) + penalty

def run() -> None:
    world, world_id = choose_world()
    if world is None or world_id is None:
        return

    results_path = os.path.join("results", world_id)
    os.makedirs(results_path, exist_ok=True)

    run_checks(world, world_id, results_path)

    finalise_report(results_path)


if __name__ == "__main__":
    run()
