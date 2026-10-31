"""check_cnn.py - READ-ONLY CNN self-test on the CPU: loads a trained model, classifies a test crop,
prints time per crop. Used by SYSTEM_CHECK.   python check_cnn.py [model.pth]"""
import glob
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("TORCH_HOME", os.path.join(HERE, "torch_home"))
sys.path.insert(0, HERE)
try:
    import numpy as np
    import torch
    from torchvision import transforms
    from PIL import Image
    import training
except Exception as exc:
    print(f"CNN_FAIL packages not available: {type(exc).__name__}: {str(exc)[:150]}")
    sys.exit(1)

model_path = sys.argv[1] if len(sys.argv) > 1 else next(iter(sorted(glob.glob(os.path.join(HERE, "models", "*.pth")))), None)
if not model_path:
    print("CNN_FAIL no trained model (*.pth) in cnn\\models")
    sys.exit(1)
try:
    torch.set_num_threads(max(1, (os.cpu_count() or 2) // 2))
    model, classes, device = training.load_cnn(model_path)
    tf = transforms.Compose([transforms.Resize((224, 224)), transforms.ToTensor(),
                             transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])
    img = Image.fromarray((np.random.RandomState(1).rand(96, 64, 3) * 255).astype("uint8"))
    x = tf(img).unsqueeze(0)
    with torch.no_grad():
        model(x)                                            # warm-up
        t0 = time.perf_counter()
        for _ in range(10):
            p = torch.softmax(model(x), dim=1)
        ms = (time.perf_counter() - t0) * 100               # 10 runs -> ms per crop
    print(f"CNN_OK model {os.path.basename(model_path)} classes {len(classes)} device {device} "
          f"torch {torch.__version__}  {ms:.0f} ms per crop (PyTorch, {torch.get_num_threads()} threads)")
except Exception as exc:
    print(f"CNN_FAIL {type(exc).__name__}: {str(exc)[:200]}")
    sys.exit(1)
