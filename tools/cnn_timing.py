"""CPU speed of one 224 x 224 crop: PyTorch vs ONNX Runtime vs OpenCV DNN (MobileNetV2).

Read-only measurement. Nothing in the sorter is changed; the result is a recommendation only.
Usage:  python tools/cnn_timing.py [model.pth] [--runs 200] [--threads N]
Without a model file a random-weight MobileNetV2 is used (speed does not depend on the weights).
The temporary ONNX file is written to the system temp folder and deleted afterwards.
"""
import argparse
import os
import statistics
import sys
import tempfile
import time

# torch first (before cv2), see CLAUDE.md
import torch
from torchvision import models

import numpy as np
import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "cnn"))


def build_model(model_path):
    if model_path:
        from training import load_cnn
        model, class_names, _device = load_cnn(model_path)
        return model.cpu().eval(), len(class_names)
    model = models.mobilenet_v2(weights=None)
    model.classifier[1] = torch.nn.Linear(model.last_channel, 2)
    return model.eval(), 2


def timed(fn, runs, warmup=10):
    for _ in range(warmup):
        fn()
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000.0)
    times.sort()
    return statistics.mean(times), times[len(times) // 2], times[int(len(times) * 0.99) - 1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model", nargs="?", help="optional .pth checkpoint from cnn/training.py")
    ap.add_argument("--runs", type=int, default=200)
    ap.add_argument("--threads", type=int, default=0, help="CPU threads (0 = default)")
    args = ap.parse_args()
    if args.threads:
        torch.set_num_threads(args.threads)
        cv2.setNumThreads(args.threads)

    model, n_classes = build_model(args.model)
    rng = np.random.default_rng(1)
    bgr = rng.integers(0, 256, (224, 224, 3), dtype=np.uint8)
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    mean = np.array([0.485, 0.456, 0.406], np.float32)
    std = np.array([0.229, 0.224, 0.225], np.float32)
    blob = ((rgb - mean) / std).transpose(2, 0, 1)[None].copy()      # 1x3x224x224

    results = {}
    tensor = torch.from_numpy(blob)

    def run_torch():
        with torch.inference_mode():
            model(tensor)
    results["PyTorch"] = timed(run_torch, args.runs)

    tmp = tempfile.mkdtemp(prefix="cnn_timing_")
    onnx_path = os.path.join(tmp, "m.onnx")
    try:
        torch.onnx.export(model, tensor, onnx_path, input_names=["x"], output_names=["y"],
                          opset_version=13, dynamo=False)
        try:
            import onnxruntime as ort
            sess = ort.InferenceSession(onnx_path, providers=["CPUExecutionProvider"])
            results["ONNX Runtime"] = timed(lambda: sess.run(None, {"x": blob}), args.runs)
        except Exception as exc:
            print("ONNX Runtime skipped:", exc)
        try:
            net = cv2.dnn.readNetFromONNX(onnx_path)
            net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)

            def run_cv():
                net.setInput(blob)
                net.forward()
            results["OpenCV DNN"] = timed(run_cv, args.runs)
        except Exception as exc:
            print("OpenCV DNN skipped:", exc)
    finally:
        for name in os.listdir(tmp):
            os.remove(os.path.join(tmp, name))
        os.rmdir(tmp)

    print(f"\n1 crop 224x224, {n_classes} classes, CPU threads: {torch.get_num_threads()}, runs: {args.runs}")
    print(f"{'backend':<14}{'mean ms':>9}{'median ms':>11}{'p99 ms':>9}{'crops/s':>9}")
    for name, (mean_ms, med, p99) in results.items():
        print(f"{name:<14}{mean_ms:>9.2f}{med:>11.2f}{p99:>9.2f}{1000.0 / mean_ms:>9.0f}")
    best = min(results, key=lambda k: results[k][0])
    print(f"\nFastest here: {best}. Recommendation only - the ejection path is not changed.")


if __name__ == "__main__":
    main()
