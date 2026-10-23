"""Apply the reproducible policy patch to the pinned local SGLang checkout."""
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CHECKOUT = ROOT / "sglang"
expected = (ROOT / "sglang-commit.txt").read_text().strip()
actual = subprocess.check_output(["git", "-C", str(CHECKOUT), "rev-parse", "HEAD"], text=True).strip()
if actual != expected:
    raise SystemExit(f"Expected SGLang {expected}, found {actual}; inspect before adapting the patch.")
patch = HERE / "sglang.patch"
target = CHECKOUT / "python/sglang/srt/mem_cache/deferred_backup.py"
source = HERE / "deferred_backup.py"
if target.exists() and target.read_bytes() != source.read_bytes():
    raise SystemExit(f"Existing {target} differs; inspect it before copying the policy module.")
command = ["git", "-C", str(CHECKOUT), "apply"]
if subprocess.run(command + ["--check", str(patch)], capture_output=True).returncode == 0:
    subprocess.run(command + [str(patch)], check=True)
elif subprocess.run(command + ["--reverse", "--check", str(patch)], capture_output=True).returncode != 0:
    raise SystemExit("Patch conflicts with local edits; no changes applied. Use a clean pinned checkout.")
shutil.copyfile(source, target)
print("Policy patch installed. Restart SGLang to load it; no pip reinstall needed for editable installs.")
