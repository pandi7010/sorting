# Upgrading to an NVIDIA GPU later

The package installs the **CPU** build of PyTorch. Real-time sorting (OpenCV + contour + HSV) never uses a GPU.
A GPU only makes **CNN inference and CNN training** (and YOLO) faster. When you add an NVIDIA GPU:

1. **Install the GPU** in the PC, then the **NVIDIA driver** (nvidia.com/drivers, "Game Ready" or "Studio" is fine). Restart Windows.
2. Open Command Prompt: `nvidia-smi`. It must show the GPU and a driver version.
   - Driver **580 or newer** → CUDA 13.0 build (**cu130**, needed for RTX 50 series).
   - Driver **528–579** → CUDA 12.6 build (**cu126**), only for older GPUs (RTX 40 series and older).
   - Older than 528 → update the driver first.
3. Close the sorter. Double-click **`UPGRADE_TO_GPU.bat`** (internet needed, download about 3 GB).
   It reads the GPU and driver, chooses the build, asks you to type **YES**, replaces `torch` and `torchvision`
   in `.venv` (same versions, 2.14.0 and 0.29.1), and verifies the result.
   - `UPGRADE_TO_GPU.bat --dry-run` shows the plan without installing.
4. **Verify:** the last lines must say `GPU available: True`, `GPU: <your GPU>`, `PASS - torch uses the GPU`.
   Later: `UPGRADE_TO_GPU.bat --verify`, or `SYSTEM_CHECK.bat` (section 6 GPU).
5. CNN training and inference use the GPU automatically (`training.py` picks `cuda` when available).

**Back to CPU** (if something goes wrong): `UPGRADE_TO_GPU.bat --back-to-cpu`.

If it says "torch does not see the GPU": update the NVIDIA driver, restart Windows, run `--verify`.
No CUDA toolkit or cuDNN installation is needed; the runtime is inside the download.

*Not tested on the development PC (it has no NVIDIA GPU): the choice logic and the dry run were tested; the real upgrade needs a GPU.*
