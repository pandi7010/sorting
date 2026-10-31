"""
UPGRADE_TO_GPU.py - replaces the CPU build of PyTorch in .venv by the matching CUDA build (one step).
Started by UPGRADE_TO_GPU.bat. Needs an NVIDIA GPU + NVIDIA driver already installed (nvidia-smi works).

    UPGRADE_TO_GPU.bat                 asks for YES, then upgrades
    UPGRADE_TO_GPU.bat --dry-run       only shows what would be done
    UPGRADE_TO_GPU.bat --verify        only checks that torch uses the GPU
    UPGRADE_TO_GPU.bat --back-to-cpu   puts the CPU build back

Only torch + torchvision (same versions as before) are replaced. The sorter's colour / HSV / contour
sorting does not use the GPU; the CNN (inference + training) and YOLO do.
"""
import argparse
import os
import shutil
import subprocess
import sys

TORCH, TORCHVISION = "torch==2.14.0", "torchvision==0.29.1"
INDEX = "https://download.pytorch.org/whl/"


def nvidia_smi():
    for c in (shutil.which("nvidia-smi"), r"C:\Windows\System32\nvidia-smi.exe",
              r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"):
        if c and os.path.exists(c):
            return c
    return None


def query(smi):
    out = subprocess.run([smi, "--query-gpu=name,driver_version,compute_cap", "--format=csv,noheader"],
                         capture_output=True, text=True, timeout=30).stdout.strip().splitlines()
    if not out:
        return None
    parts = [p.strip() for p in out[0].split(",")]
    name, driver = parts[0], parts[1]
    cc = parts[2] if len(parts) > 2 else ""
    return name, driver, cc


def choose(driver, cc):
    """-> (cuda build tag, None) or (None, reason). CUDA 13.0 needs driver >= 580, CUDA 12.6 >= 528.
    GPUs with compute capability >= 12 (RTX 50xx / Blackwell) need CUDA 12.8 or newer -> cu130."""
    try:
        drv = float(driver.split(".")[0] + "." + (driver.split(".") + ["0"])[1])
    except Exception:
        return None, f"cannot read the driver version '{driver}'"
    try:
        ccv = float(cc) if cc else 0.0
    except ValueError:
        ccv = 0.0
    if drv >= 580:
        return "cu130", None
    if ccv >= 12.0:
        return None, f"this GPU (compute capability {cc}, RTX 50 series) needs NVIDIA driver 580 or newer; installed {driver}"
    if drv >= 528:
        return "cu126", None
    return None, f"NVIDIA driver {driver} is too old for CUDA 12 (need 528 or newer) - update the NVIDIA driver"


def verify():
    code = ("import torch, torchvision;"
            "print('torch', torch.__version__, '| torchvision', torchvision.__version__, '| CUDA build', torch.version.cuda);"
            "ok = torch.cuda.is_available();"
            "print('GPU available:', ok);"
            "ok and print('GPU:', torch.cuda.get_device_name(0));"
            "x = torch.randn(512, 512, device='cuda') if ok else None;"
            "ok and print('GPU matrix test:', float((x @ x).sum().abs() > 0));"
            "raise SystemExit(0 if ok else 1)")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300)
    print(r.stdout.strip() or r.stderr.strip()[-400:])
    return r.returncode == 0


def pip_install(index_tag, dry):
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--force-reinstall", "--no-deps",
           TORCH, TORCHVISION, "--index-url", INDEX + index_tag]
    print("\n" + " ".join(cmd) + "\n")
    if dry:
        print("(dry run - nothing installed)")
        return True
    return subprocess.run(cmd).returncode == 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--back-to-cpu", action="store_true")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    ap.add_argument("--cuda", choices=["cu126", "cu130"], help="force a CUDA build (normally chosen automatically)")
    a = ap.parse_args()
    if a.back_to_cpu:
        ok = pip_install("cpu", a.dry_run)
        print("CPU build restored." if ok else "FAILED - see the messages above.")
        return 0 if ok else 1
    if a.verify:
        ok = verify()
        print("PASS - torch uses the GPU" if ok else "FAIL - torch does not use the GPU (still CPU build or driver problem)")
        return 0 if ok else 1
    smi = nvidia_smi()
    if not smi:
        print("FAIL: nvidia-smi not found - no NVIDIA driver installed. Install the NVIDIA driver for the GPU "
              "(nvidia.com/drivers), restart Windows, then run this again.")
        return 1
    info = query(smi)
    if not info:
        print("FAIL: nvidia-smi finds no GPU. Check that the GPU is seated and the driver is installed.")
        return 1
    name, driver, cc = info
    print(f"GPU: {name} | driver {driver} | compute capability {cc or '?'}")
    tag, why = (a.cuda, None) if a.cuda else choose(driver, cc)
    if not tag:
        print("FAIL: " + why)
        return 1
    print(f"Chosen build: {tag} (CUDA {'13.0' if tag == 'cu130' else '12.6'}) - download about 2.5-3.5 GB, internet needed.")
    print("Replaces the CPU torch + torchvision in .venv (the CUDA runtime is inside the download; no CUDA toolkit needed).")
    if not a.dry_run and not a.yes and input("Type YES to continue: ").strip() != "YES":
        print("Cancelled.")
        return 1
    if not pip_install(tag, a.dry_run):
        print("FAILED - see above. The CPU build may be damaged: run  UPGRADE_TO_GPU.bat --back-to-cpu")
        return 1
    if a.dry_run:
        return 0
    ok = verify()
    print("\nPASS - torch uses the GPU. CNN training and inference now run on it." if ok else
          "\nFAIL - installed, but torch does not see the GPU. Update the NVIDIA driver and restart Windows, then "
          "run  UPGRADE_TO_GPU.bat --verify.  Back to CPU:  UPGRADE_TO_GPU.bat --back-to-cpu")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
