LAMTON V2 - CNN (PyTorch MobileNetV2, CPU)
==========================================
training.py       train / load / predict / evaluate a CNN (from the CNN project; no UI, no licence lock)
models\           trained models: cashew_color_cnn.pth (11 cashew grades), cashew_color_cnn_new.pth and
                  cnn_Default_20261006_235341.pth (GOOD/TESTA, only 72 images - a test model!)
torch_home\       ImageNet starting weights for MobileNetV2 (training works without internet)
RUN_CNN_TRAIN.bat training from the command line      check_cnn.py   CNN self-test (SYSTEM_CHECK runs it)
Not included on purpose: datasets, the cashew grader application (another customer's hardware lock).
The sorter's colour / HSV / contour sorting does NOT depend on the CNN. If the CNN packages are
missing the sorter still starts. On a GPU later: UPGRADE_TO_GPU.bat in the package folder.
