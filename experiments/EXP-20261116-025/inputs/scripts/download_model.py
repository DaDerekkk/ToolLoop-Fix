#!/usr/bin/env python3
"""Download a pinned public model without executing repository code."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def now():
    return datetime.now(timezone.utc).isoformat()


def sha256(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    model_id, revision = config["model_id"], config["revision"]
    target = ROOT / "models" / model_id.split("/")[-1] / revision
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    target.mkdir(parents=True, exist_ok=True)
    (output / "config.json").write_bytes(args.config.read_bytes())
    (output / "download_model.py").write_bytes(Path(__file__).read_bytes())
    record = {"operation": "pinned_public_model_download", "status": "running", "started_at_utc": now(),
              "model_id": model_id, "revision": revision, "model_path": str(target), "files": [],
              "authorization": config["authorization"], "commands": []}
    def save():
        (output / "manifest.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    save()
    def fetch(url, destination, resume=False):
        command = ["curl", "-fSL", "--retry", "2", "--connect-timeout", "30", "--max-time", "1800"]
        if resume:
            command += ["-C", "-"]
        command += [url, "-o", str(destination)]
        record["commands"].append(command)
        save()
        with (output / "download.log").open("a") as log:
            completed = subprocess.run(command, stdout=log, stderr=log)
        if completed.returncode:
            raise RuntimeError("download failed exit {} for {}".format(completed.returncode, url))
    try:
        api = "https://huggingface.co/api/models/{}/revision/{}?blobs=true".format(model_id, revision)
        fetch(api, output / "repository_metadata.json")
        metadata = json.loads((output / "repository_metadata.json").read_text())
        if metadata["sha"] != revision:
            raise ValueError("Repository revision mismatch")
        allowed = {"config.json", "generation_config.json", "tokenizer.json", "tokenizer_config.json",
                   "chat_template.jinja", "vocab.json", "merges.txt", "model.safetensors.index.json", "LICENSE", "LICENSE.txt"}
        selected = [item for item in metadata["siblings"] if item["rfilename"] in allowed
                    or (item["rfilename"].startswith("model") and item["rfilename"].endswith(".safetensors"))]
        if not any(item["rfilename"].endswith(".safetensors") for item in selected):
            raise ValueError("No safetensors weights in pinned metadata")
        for item in selected:
            name = item["rfilename"]
            destination = target / name
            destination.resolve().relative_to(target.resolve())
            url = "https://huggingface.co/{}/resolve/{}/{}".format(model_id, revision, name)
            if not destination.exists():
                part = destination.with_name(destination.name + ".part")
                fetch(url, part, resume=part.exists())
                part.replace(destination)
            observed = sha256(destination)
            size = destination.stat().st_size
            lfs = item.get("lfs") or {}
            if lfs.get("sha256") and lfs["sha256"] != observed:
                raise ValueError("Weight SHA256 mismatch: " + name)
            if item.get("size") is not None and item["size"] != size:
                raise ValueError("File size mismatch: " + name)
            if not lfs and item.get("blobId"):
                content = destination.read_bytes()
                blob = hashlib.sha1(("blob {}\0".format(len(content))).encode() + content).hexdigest()
                if blob != item["blobId"]:
                    raise ValueError("Git blob mismatch: " + name)
            record["files"].append({"path": name, "sha256": observed, "size_bytes": size,
                                    "url": url, "official_lfs_sha256": lfs.get("sha256"),
                                    "official_blob_id": item.get("blobId")})
            save()
            print("verified", name, size, flush=True)
        model_manifest = {"schema_version": 1, "model_id": model_id, "revision": revision,
                          "model_path": str(target), "downloaded_at_utc": now(), "files": record["files"],
                          "source": api, "trust_remote_code": False, "quantization": None,
                          "license_metadata": metadata.get("cardData", {}).get("license")}
        manifests = ROOT / "models/manifests"
        manifests.mkdir(parents=True, exist_ok=True)
        manifest_path = manifests / (model_id.split("/")[-1] + "-" + revision + ".json")
        if manifest_path.exists():
            raise FileExistsError("Refusing to overwrite existing model manifest")
        manifest_path.write_text(json.dumps(model_manifest, ensure_ascii=False, indent=2) + "\n")
        (output / "model_manifest.json").write_bytes(manifest_path.read_bytes())
        record.update(status="completed", model_manifest_path=str(manifest_path),
                      model_manifest_sha256=sha256(manifest_path), total_bytes=sum(x["size_bytes"] for x in record["files"]))
        code = 0
    except Exception as exc:
        record.update(status="failed", error="{}: {}".format(type(exc).__name__, exc))
        code = 1
    record.update(ended_at_utc=now(), exit_code=code)
    record["artifacts"] = {p.name: sha256(p) for p in output.iterdir() if p.is_file() and p.name != "manifest.json"}
    save()
    print(json.dumps({"status": record["status"], "error": record.get("error"), "model_path": str(target)}, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
