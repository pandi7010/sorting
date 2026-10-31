# பின்னர் NVIDIA GPU-க்கு மாறுதல்

இந்த package PyTorch-இன் **CPU** build-ஐ நிறுவுகிறது. நேரடி sorting (OpenCV + contour + HSV) GPU-ஐ ஒருபோதும் பயன்படுத்தாது.
GPU, **CNN inference, CNN training** (மற்றும் YOLO)-ஐ மட்டுமே வேகப்படுத்தும். NVIDIA GPU சேர்க்கும் போது:

1. கணினியில் **GPU**-ஐப் பொருத்தி, பிறகு **NVIDIA driver** நிறுவவும் (nvidia.com/drivers). Windows-ஐ restart செய்யவும்.
2. Command Prompt-இல் `nvidia-smi` இயக்கவும். GPU மற்றும் driver version தெரிய வேண்டும்.
   - Driver **580 அல்லது புதியது** → CUDA 13.0 build (**cu130**, RTX 50 series-க்கு தேவை).
   - Driver **528–579** → CUDA 12.6 build (**cu126**), பழைய GPU-களுக்கு மட்டும் (RTX 40 மற்றும் பழையவை).
   - 528-க்கு குறைவு → முதலில் driver-ஐ update செய்யவும்.
3. Sorter-ஐ மூடவும். **`UPGRADE_TO_GPU.bat`** double-click செய்யவும் (internet தேவை, சுமார் 3 GB download).
   அது GPU-ஐயும் driver-ஐயும் படித்து build-ஐத் தேர்ந்தெடுத்து, **YES** என type செய்யச் சொல்லி, `.venv`-இல்
   `torch`, `torchvision`-ஐ (அதே versions: 2.14.0, 0.29.1) மாற்றி, முடிவைச் சரிபார்க்கும்.
   - `UPGRADE_TO_GPU.bat --dry-run` நிறுவாமல் திட்டத்தை மட்டும் காட்டும்.
4. **சரிபார்ப்பு:** கடைசி வரிகள் `GPU available: True`, `GPU: <உங்கள் GPU>`, `PASS - torch uses the GPU` காட்ட வேண்டும்.
   பின்னர்: `UPGRADE_TO_GPU.bat --verify`, அல்லது `SYSTEM_CHECK.bat` (பகுதி 6 GPU).
5. CNN training மற்றும் inference GPU-ஐத் தானாகப் பயன்படுத்தும் (`training.py` இருந்தால் `cuda` தேர்ந்தெடுக்கும்).

**மீண்டும் CPU-க்கு** (ஏதாவது தவறானால்): `UPGRADE_TO_GPU.bat --back-to-cpu`.

"torch does not see the GPU" என்றால்: NVIDIA driver-ஐ update செய்து, Windows-ஐ restart செய்து, `--verify` இயக்கவும்.
CUDA toolkit அல்லது cuDNN நிறுவ தேவையில்லை; runtime download-க்குள்ளேயே உள்ளது.

*Development கணினியில் NVIDIA GPU இல்லாததால் உண்மையான மாற்றம் சோதிக்கப்படவில்லை: தேர்வு logic-உம் dry run-உம் மட்டும் சோதிக்கப்பட்டன.*
