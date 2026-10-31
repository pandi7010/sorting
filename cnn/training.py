"""Model training and label checking, without any UI.

    train_cnn(...)        MobileNetV2 classifier on the class folders, with a held-out validation part
    verify_labels(...)    run a CNN on the labelled images and list those where label and prediction disagree
    train_yolo_detect(...) YOLO DETECTION model (boxes) from images with YOLO .txt labels

Every function reports progress through a callback so the UI can show it live. Results (accuracy per
class, when, on how many images) are written next to the model as <model>.json.
"""
import json
import os
import random
import shutil
import time
from datetime import datetime
from pathlib import Path

IMAGE_TYPES = (".jpg", ".jpeg", ".png", ".bmp")
MIN_VAL_PER_CLASS = 2


def list_class_images(dataset_dir):
    """{class folder name: [image paths]} for every class folder."""
    out = {}
    if os.path.isdir(dataset_dir):
        for name in sorted(os.listdir(dataset_dir)):
            folder = os.path.join(dataset_dir, name)
            if os.path.isdir(folder):
                out[name] = [os.path.join(folder, f) for f in sorted(os.listdir(folder)) if f.lower().endswith(IMAGE_TYPES)]
    return out


def metrics_path(model_path):
    return str(Path(model_path).with_suffix(".json"))


def read_metrics(model_path):
    """The saved training result of a model, or None when it was trained without one."""
    try:
        with open(metrics_path(model_path), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def split_train_val(images_by_class, val_fraction=0.2, seed=1):
    """Shuffled split per class, so every class is in both parts."""
    rng = random.Random(seed)
    train, val = [], []
    for index, (name, paths) in enumerate(images_by_class.items()):
        paths = list(paths)
        rng.shuffle(paths)
        n_val = max(MIN_VAL_PER_CLASS, int(round(len(paths) * val_fraction)))
        n_val = min(n_val, max(0, len(paths) - 1))
        val += [(path, index) for path in paths[:n_val]]
        train += [(path, index) for path in paths[n_val:]]
    return train, val


def _square(image):
    """Pad to a square by repeating the border, exactly as the grader does before the CNN."""
    import cv2
    h, w = image.shape[:2]
    side = max(h, w)
    pad_h, pad_w = (side - h) // 2, (side - w) // 2
    return cv2.copyMakeBorder(image, pad_h, pad_h, pad_w, pad_w, cv2.BORDER_REPLICATE)


def _load_rgb(path):
    import cv2
    from PIL import Image
    image = cv2.imread(path)
    if image is None:
        raise ValueError(f"cannot read image {path}")
    return Image.fromarray(cv2.cvtColor(_square(image), cv2.COLOR_BGR2RGB))


def train_cnn(dataset_dir, classes, epochs, out_dir, name_prefix="cnn", progress=None, stop=None, batch_size=32):
    """Train MobileNetV2 on the chosen class folders.

    progress(dict) is called after every epoch with: epoch, epochs, loss, val_acc, seconds_left.
    stop() returning True ends the training after the current epoch (the best model so far is saved).
    Returns the result dict that is also written to <model>.json. Raises ValueError for a bad dataset.
    """
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, Dataset
    from torchvision import models, transforms

    all_images = list_class_images(dataset_dir)
    images = {name: all_images.get(name, []) for name in classes}
    empty = [name for name, paths in images.items() if len(paths) < MIN_VAL_PER_CLASS + 1]
    if empty:
        raise ValueError(f"These classes have fewer than {MIN_VAL_PER_CLASS + 1} images: {', '.join(empty)}")
    if len(images) < 2:
        raise ValueError("At least 2 classes are needed")
    class_names = list(images.keys())
    train_items, val_items = split_train_val(images)

    normalize = transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])
    train_tf = transforms.Compose([
        transforms.RandomResizedCrop(224, scale=(0.8, 1.0)), transforms.RandomHorizontalFlip(), transforms.RandomVerticalFlip(),
        transforms.RandomRotation(15), transforms.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.2),
        transforms.ToTensor(), normalize])
    eval_tf = transforms.Compose([transforms.Resize((224, 224)), transforms.ToTensor(), normalize])    # same as the grader

    class Items(Dataset):
        def __init__(self, items, transform):
            self.items, self.transform = items, transform

        def __len__(self):
            return len(self.items)

        def __getitem__(self, i):
            path, label = self.items[i]
            return self.transform(_load_rgb(path)), label

    train_loader = DataLoader(Items(train_items, train_tf), batch_size=min(batch_size, len(train_items)), shuffle=True, num_workers=0)
    val_loader = DataLoader(Items(val_items, eval_tf), batch_size=batch_size, shuffle=False, num_workers=0)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    try:
        model = models.mobilenet_v2(weights=models.MobileNet_V2_Weights.DEFAULT)
        pretrained = True
    except Exception as exc:                      # no internet and no cached weights
        print(f"[TRAIN] Pre-trained MobileNetV2 weights not available ({exc}); training from scratch")
        model = models.mobilenet_v2(weights=None)
        pretrained = False
    model.classifier[1] = nn.Linear(model.last_channel, len(class_names))
    model = model.to(device)
    # Rare classes count as much as frequent ones.
    counts = torch.tensor([len(images[name]) for name in class_names], dtype=torch.float32)
    criterion = nn.CrossEntropyLoss(weight=(counts.sum() / (len(counts) * counts)).to(device))
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3 if not pretrained else 3e-4)

    def evaluate():
        model.eval()
        correct = [0] * len(class_names)
        total = [0] * len(class_names)
        with torch.no_grad():
            for inputs, labels in val_loader:
                predicted = model(inputs.to(device)).argmax(dim=1).cpu()
                for label, pred in zip(labels.tolist(), predicted.tolist()):
                    total[label] += 1
                    correct[label] += int(label == pred)
        return correct, total

    history, best, started = [], None, time.time()
    for epoch in range(1, int(epochs) + 1):
        model.train()
        running, seen = 0.0, 0
        for inputs, labels in train_loader:
            inputs, labels = inputs.to(device), labels.to(device)
            optimizer.zero_grad()
            loss = criterion(model(inputs), labels)
            loss.backward()
            optimizer.step()
            running += loss.item() * inputs.size(0)
            seen += inputs.size(0)
        correct, total = evaluate()
        val_acc = sum(correct) / max(1, sum(total))
        # Mean of the per-class accuracies: a model that ignores a small class does not look good.
        balanced = sum(c / t for c, t in zip(correct, total) if t) / max(1, sum(1 for t in total if t))
        row = {"epoch": epoch, "loss": round(running / max(1, seen), 4), "val_acc": round(val_acc, 4), "val_balanced_acc": round(balanced, 4)}
        history.append(row)
        if best is None or balanced >= best["balanced"]:
            best = {"balanced": balanced, "epoch": epoch, "correct": correct, "total": total, "val_acc": val_acc,
                    "state": {k: v.detach().cpu().clone() for k, v in model.state_dict().items()}}
        if progress:
            elapsed = time.time() - started
            progress(dict(row, epochs=int(epochs), seconds_left=elapsed / epoch * (int(epochs) - epoch)))
        if stop and stop():
            break

    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now()
    model_path = os.path.join(out_dir, f"{name_prefix}_{stamp:%Y%m%d_%H%M%S}.pth")
    torch.save({"model_state_dict": best["state"], "class_names": class_names}, model_path)
    result = {
        "type": "cnn", "architecture": "MobileNetV2", "model": os.path.basename(model_path), "trained": stamp.strftime("%Y-%m-%d %H:%M"),
        "dataset": str(dataset_dir), "classes": class_names, "images": {name: len(images[name]) for name in class_names},
        "train_images": len(train_items), "val_images": len(val_items), "epochs_run": len(history), "best_epoch": best["epoch"],
        "val_accuracy": round(best["val_acc"], 4), "val_balanced_accuracy": round(best["balanced"], 4),
        "per_class": {name: {"correct": best["correct"][i], "total": best["total"][i],
                             "accuracy": round(best["correct"][i] / best["total"][i], 4) if best["total"][i] else None}
                      for i, name in enumerate(class_names)},
        "pretrained": pretrained, "device": str(device), "history": history,
        "note": "Accuracy is measured on validation images that were not used for training.",
    }
    with open(metrics_path(model_path), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    result["model_path"] = model_path
    return result


def load_cnn(model_path):
    """(model in eval mode, class names, device) of a saved CNN checkpoint."""
    import torch
    from torchvision import models
    checkpoint = torch.load(str(model_path), map_location="cpu")
    if not (isinstance(checkpoint, dict) and "class_names" in checkpoint):
        raise ValueError("This model file has no class names stored; it cannot be used for the label check")
    class_names = list(checkpoint["class_names"])
    model = models.mobilenet_v2(weights=None)
    model.classifier[1] = torch.nn.Linear(model.last_channel, len(class_names))
    model.load_state_dict(checkpoint.get("model_state_dict", checkpoint.get("model_state")))
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return model.to(device).eval(), class_names, device


def verify_labels(model_path, dataset_dir, progress=None, batch_size=32):
    """Run the CNN on every labelled image. Returns a dict:
        mismatches     [{path, label, predicted, confidence}] sorted by confidence (most certain first)
        checked        images checked
        skipped        {class folder: image count} whose class the model does not know (cannot be checked)
        model_classes  class names of the model
    """
    import torch
    from torchvision import transforms
    model, class_names, device = load_cnn(model_path)
    known = {name.upper().strip(): name for name in class_names}
    eval_tf = transforms.Compose([transforms.Resize((224, 224)), transforms.ToTensor(),
                                  transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])
    items, skipped = [], {}
    for folder, paths in list_class_images(dataset_dir).items():
        if folder.upper().strip() in known:
            items += [(path, known[folder.upper().strip()]) for path in paths]
        elif paths:
            skipped[folder] = len(paths)
    mismatches = []
    for start in range(0, len(items), batch_size):
        batch = items[start:start + batch_size]
        tensors, kept = [], []
        for path, label in batch:
            try:
                tensors.append(eval_tf(_load_rgb(path)))
                kept.append((path, label))
            except ValueError:
                continue
        if not tensors:
            continue
        with torch.no_grad():
            probabilities = torch.softmax(model(torch.stack(tensors).to(device)), dim=1).cpu()
        for (path, label), probs in zip(kept, probabilities):
            index = int(probs.argmax())
            if class_names[index] != label:
                mismatches.append({"path": path, "label": label, "predicted": class_names[index], "confidence": round(float(probs[index]), 3)})
        if progress:
            progress(min(len(items), start + batch_size), len(items))
    mismatches.sort(key=lambda m: -m["confidence"])
    return {"mismatches": mismatches, "checked": len(items), "skipped": skipped, "model_classes": class_names}


def predict_images(model_path, paths, batch_size=32):
    """Class and confidence of a CNN for a list of images: {path: (class name, confidence)}. Nothing is moved."""
    import torch
    from torchvision import transforms
    model, class_names, device = load_cnn(model_path)
    eval_tf = transforms.Compose([transforms.Resize((224, 224)), transforms.ToTensor(),
                                  transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])
    out = {}
    for start in range(0, len(paths), batch_size):
        tensors, kept = [], []
        for path in paths[start:start + batch_size]:
            try:
                tensors.append(eval_tf(_load_rgb(path)))
                kept.append(path)
            except ValueError:
                continue
        if not tensors:
            continue
        with torch.no_grad():
            probabilities = torch.softmax(model(torch.stack(tensors).to(device)), dim=1).cpu()
        for path, probs in zip(kept, probabilities):
            index = int(probs.argmax())
            out[path] = (class_names[index], float(probs[index]))
    return out


def evaluate_cnn(model_path, dataset_dir, batch_size=32):
    """Accuracy of a CNN on labelled class folders (the fixed test set): {CLASS NAME: (correct, total)}.
    Only folders whose class the model knows are in the result."""
    import torch
    from torchvision import transforms
    model, class_names, device = load_cnn(model_path)
    known = {name.upper().strip(): index for index, name in enumerate(class_names)}
    eval_tf = transforms.Compose([transforms.Resize((224, 224)), transforms.ToTensor(),
                                  transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])
    result = {}
    for folder, paths in list_class_images(dataset_dir).items():
        key = folder.upper().strip()
        if key not in known or not paths:
            continue
        correct = total = 0
        for start in range(0, len(paths), batch_size):
            tensors = []
            for path in paths[start:start + batch_size]:
                try:
                    tensors.append(eval_tf(_load_rgb(path)))
                except ValueError:
                    continue
            if not tensors:
                continue
            with torch.no_grad():
                predicted = model(torch.stack(tensors).to(device)).argmax(dim=1).cpu().tolist()
            correct += sum(1 for p in predicted if p == known[key])
            total += len(predicted)
        result[key] = (correct, total)
    return result


# ------------------------------------------------------------------ YOLO detection
def detection_dataset_stats(images_dir, class_names):
    """Images, labelled images and boxes per class of a folder with YOLO .txt labels next to the images."""
    images = [f for f in sorted(os.listdir(images_dir)) if f.lower().endswith(IMAGE_TYPES)] if os.path.isdir(images_dir) else []
    stats = {"images": len(images), "labelled": 0, "boxes": 0, "per_class": {name: 0 for name in class_names}, "bad_lines": 0, "labelled_files": []}
    for name in images:
        label_file = os.path.join(images_dir, os.path.splitext(name)[0] + ".txt")
        if not os.path.exists(label_file):
            continue
        boxes = 0
        with open(label_file, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                parts = line.split()
                if not parts:
                    continue
                try:
                    index = int(parts[0])
                    values = [float(v) for v in parts[1:]]
                    # a box is "x y w h"; a polygon (magic wand) is "x1 y1 x2 y2 ..." - YOLO turns polygons into boxes
                    is_box, is_polygon = len(values) == 4, len(values) >= 6 and len(values) % 2 == 0
                    if not (is_box or is_polygon) or not 0 <= index < len(class_names) or not all(0.0 <= v <= 1.0 for v in values):
                        raise ValueError
                except ValueError:
                    stats["bad_lines"] += 1
                    continue
                stats["per_class"][class_names[index]] += 1
                boxes += 1
        if boxes:
            stats["labelled"] += 1
            stats["boxes"] += boxes
            stats["labelled_files"].append(name)
    return stats


def train_yolo_detect(images_dir, class_names, epochs, base_model, out_dir, work_dir, name_prefix="yolo",
                      progress=None, stop=None, imgsz=640, single_class=False):
    """Train a YOLO DETECTION model (boxes + classes), the kind the grader needs.

    images_dir holds images with a YOLO .txt label file next to each labelled image.
    progress(dict): epoch, epochs, box_loss, map50, seconds_left. Raises ValueError when there is nothing to train on.
    """
    from ultralytics import YOLO
    if str(base_model).lower().endswith("-cls.pt"):
        raise ValueError("A classification base model (-cls) cannot be used: the grader needs a detection model")
    stats = detection_dataset_stats(images_dir, class_names)
    if stats["labelled"] < 10:
        raise ValueError(f"Only {stats['labelled']} image(s) with box labels in {images_dir}. Draw boxes on at least 10 images "
                         "(Teaching YOLO page) before training a detection model.")
    files = list(stats["labelled_files"])
    random.Random(1).shuffle(files)
    n_val = max(2, int(round(len(files) * 0.2)))
    work = Path(work_dir)
    if work.exists():
        shutil.rmtree(work)
    for part, names in (("val", files[:n_val]), ("train", files[n_val:])):
        (work / "images" / part).mkdir(parents=True)
        (work / "labels" / part).mkdir(parents=True)
        for name in names:
            shutil.copy(os.path.join(images_dir, name), work / "images" / part / name)
            source = os.path.join(images_dir, os.path.splitext(name)[0] + ".txt")
            target = work / "labels" / part / (os.path.splitext(name)[0] + ".txt")
            if single_class:
                # Detection only: every box becomes class 0 "CASHEW". The label files in the dataset are not changed.
                with open(source, "r", encoding="utf-8", errors="ignore") as f:
                    lines = [" ".join(["0"] + line.split()[1:]) for line in f if len(line.split()) >= 5]
                target.write_text("\n".join(lines) + "\n", encoding="utf-8")
            else:
                shutil.copy(source, target)
    if single_class:
        class_names = ["CASHEW"]
    data_yaml = work / "data.yaml"
    with open(data_yaml, "w", encoding="utf-8") as f:
        f.write(f"path: {work.as_posix()}\ntrain: images/train\nval: images/val\nnames:\n")
        for index, name in enumerate(class_names):
            f.write(f"  {index}: {json.dumps(name)}\n")

    model = YOLO(str(base_model))
    started, history = time.time(), []

    def on_epoch(trainer):
        if trainer.epoch + 1 <= len(history):      # called once more for the final validation of the best weights
            return
        metrics = dict(trainer.metrics or {})
        loss = 0.0
        try:                                       # mean training losses of the epoch, by name
            named = trainer.label_loss_items(trainer.tloss, prefix="train")
            loss = float(next((v for k, v in named.items() if "box" in k), 0.0))
        except Exception:
            pass
        row = {"epoch": trainer.epoch + 1, "epochs": int(epochs), "box_loss": round(loss, 4),
               "map50": round(float(metrics.get("metrics/mAP50(B)", 0.0)), 4)}
        history.append(row)
        if progress:
            elapsed = time.time() - started
            progress(dict(row, seconds_left=elapsed / row["epoch"] * (int(epochs) - row["epoch"])))
        if stop and stop():
            trainer.stop = True
    model.add_callback("on_fit_epoch_end", on_epoch)
    results = model.train(data=str(data_yaml), epochs=int(epochs), imgsz=imgsz, workers=0, verbose=False,
                          project=str(work / "runs"), name="detect", exist_ok=True, plots=False)
    best = Path(results.save_dir) / "weights" / "best.pt"
    if not best.exists():
        raise RuntimeError("Training ended without a best.pt file")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.now()
    model_path = os.path.join(out_dir, f"{name_prefix}_{stamp:%Y%m%d_%H%M%S}.pt")
    shutil.copy(best, model_path)
    box = getattr(results, "box", None)
    per_class = {}
    if box is not None and getattr(box, "ap_class_index", None) is not None:
        for position, class_index in enumerate(box.ap_class_index):
            per_class[class_names[int(class_index)]] = {"map50": round(float(box.ap50[position]), 4)}
    result = {
        "type": "yolo-detect", "architecture": os.path.basename(str(base_model)), "model": os.path.basename(model_path),
        "trained": stamp.strftime("%Y-%m-%d %H:%M"), "dataset": str(images_dir), "classes": list(class_names),
        "images": stats["labelled"], "boxes": stats["boxes"], "boxes_per_class": stats["per_class"],
        "train_images": len(files) - n_val, "val_images": n_val, "epochs_run": len(history),
        "map50": round(float(box.map50), 4) if box is not None else None, "map50_95": round(float(box.map), 4) if box is not None else None,
        "per_class": per_class, "history": history, "note": "mAP is measured on validation images that were not used for training.",
    }
    with open(metrics_path(model_path), "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    result["model_path"] = model_path
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Train the CNN from the command line (same code as the Teaching CNN page).")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--classes", default="", help="comma separated; default: every class folder with at least 3 images")
    args = parser.parse_args()
    chosen = [c for c in args.classes.split(",") if c] or [n for n, p in list_class_images(args.dataset).items() if len(p) >= 3]
    outcome = train_cnn(args.dataset, chosen, args.epochs, args.out,
                        progress=lambda p: print(f"epoch {p['epoch']}/{p['epochs']}  loss {p['loss']:.3f}  val acc {p['val_acc']:.3f}  "
                                                 f"left {p['seconds_left']:.0f}s", flush=True))
    print(json.dumps({k: v for k, v in outcome.items() if k != "history"}, indent=2))
