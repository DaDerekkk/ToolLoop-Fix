"""Public, one-shot worker for a disposable Colab VM. No account credentials."""
import importlib.metadata as md
import os
from pathlib import Path
import subprocess
import sys
import time

import probe
import tx


HERE = Path(__file__).resolve().parent
VERSION = "5.23.0"
PROBE_STAGES = {"runtime_check", "anonymous_public_download", "verify_public_snapshot",
                "local_model_load", "render_public_prompt", "generate"}


def publish(name, text):
    temporary = HERE / ("." + name + ".tmp")
    temporary.write_text(text + "\n", encoding="utf-8")
    os.replace(temporary, HERE / name)


class Observed(dict):
    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        if key == "stage" and value in PROBE_STAGES:
            publish("stage.txt", value)


def install():
    try:
        installed = md.version("transformers")
    except md.PackageNotFoundError:
        installed = None
    if installed == VERSION:
        return
    publish("stage.txt", "install_transformers")
    # Base transformers does not require installing torch; pin the VM's actual
    # torch build as a constraint so resolution cannot replace it inadvertently.
    torch_version = md.version("torch")
    constraint = HERE / "keep_torch.txt"
    with constraint.open("x", encoding="utf-8") as out:
        out.write("torch==" + torch_version + "\n")
    subprocess.run([sys.executable, "-m", "pip", "--disable-pip-version-check", "install",
                    "--no-input", "--progress-bar", "off", "--constraint", str(constraint),
                    "transformers==" + VERSION], check=True, timeout=110,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if md.version("torch") != torch_version or md.version("transformers") != VERSION:
        raise RuntimeError("package_versions_differ")


def diagnostic(exc):
    # Only bounded enum labels and numeric HTTP status. Never save exception text.
    codes = []
    for _ in range(4):
        if exc is None:
            break
        enum = type(exc).__name__
        known = {"ImportError", "ModuleNotFoundError", "PackageNotFoundError", "ValueError",
                 "RuntimeError", "PermissionError", "OSError", "TimeoutError", "TimeoutExpired",
                 "CalledProcessError", "HTTPError", "HfHubHTTPError", "LocalEntryNotFoundError",
                 "ReadTimeout", "ConnectTimeout", "ConnectionError", "OutOfMemoryError"}
        codes.append(enum if enum in known else "other_exception")
        missing = getattr(exc, "name", None)
        if missing in ("torch", "transformers", "huggingface_hub", "torchvision", "torchaudio",
                       "librosa", "accelerate", "tokenizers", "safetensors"):
            codes.append("module." + missing)
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if type(status) is int and 100 <= status <= 599:
            codes.append("http." + str(status))
        for token, label in (("torchvision::nms", "torchvision_nms"), ("no space left", "disk_full"),
                             ("out of memory", "oom"), ("unexpected eos", "unexpected_eos"),
                             ("hash mismatch", "digest_mismatch"), ("cuda required", "no_cuda"),
                             ("requires an l4", "gpu_dtype"), ("package_versions_differ", "package_mismatch")):
            if token in str(exc).lower():
                codes.append(label)
        exc = exc.__cause__ or exc.__context__
    publish("diagnostic.txt", ",".join(codes))


def main():
    nonce = (HERE / "nonce.txt").read_text().strip()
    tx.check_nonce(nonce)
    try:
        with (HERE / "started.lock").open("x") as lock:
            lock.write(nonce + "\n")
    except FileExistsError:
        return 3  # A duplicate worker must not alter the first worker's state.
    os.environ.update(HF_HUB_DISABLE_TELEMETRY="1", HF_HUB_DISABLE_IMPLICIT_TOKEN="1",
                      HF_HUB_DISABLE_PROGRESS_BARS="1", TQDM_DISABLE="1", HF_HUB_DISABLE_XET="1",
                      TOKENIZERS_PARALLELISM="false", PIP_NO_INPUT="1")
    publish("stage.txt", "waiting_for_arm")
    deadline = time.monotonic() + 450

    def execute(report):
        seen = Observed(report)
        try:
            seen["stage"] = "waiting_for_arm"
            while True:
                arm = HERE / "armed.txt"
                if arm.is_file() and arm.read_text().strip() == nonce:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("arm deadline")
                time.sleep(0.5)
            seen["stage"] = "package_check"
            publish("stage.txt", "package_check")
            install()
            probe.run_probe(seen)
        except Exception as exc:
            if seen["stage"] == "package_check" and (HERE / "stage.txt").read_text().strip() == "install_transformers":
                seen["stage"] = "install_transformers"
            diagnostic(exc)
            raise
        finally:
            report.update(seen)
    original = HERE / "toolloop_probe.json"
    code = probe.main(original, execute)
    frame = tx.emit(original, nonce)
    publish("result_frame.txt", frame)
    publish("stage.txt", "receipt_published_completed" if code == 0 else "receipt_published_failed")
    return code


if __name__ == "__main__":
    sys.exit(main())
