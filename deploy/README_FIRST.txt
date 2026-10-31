=====================================================================
 LAMTON V2 - DEPLOYMENT PACKAGE  (63-channel colour sorter)
=====================================================================
Read INSTALL_NEW_PC.md (English) or INSTALL_NEW_PC_TAMIL.md (Tamil) - step by step.

Short version:
  1. drivers\  : install the MindVision SDK, the CP210x USB driver, Python 3.12 (if missing)
  2. Copy this whole folder to  C:\LAMTON_V2   (do NOT run it from the USB drive;
     never overwrite or delete the old sorter program on that PC)
  3. INSTALL.bat        creates .venv, installs sorter + CNN packages, CPU only (internet,
                        or offline with a wheels\ folder - see MAKE_OFFLINE_WHEELS.bat)
  4. SYSTEM_CHECK.bat   READ-ONLY: Python, SDK, cameras, disk, ESP32 boards, GPU, licence
  5. tools\CAMERA_PROBE.bat   back up the camera settings BEFORE anything else
  6. Set COM ports + camera serial numbers (INSTALL_NEW_PC.md, step 9), licence (step 10)
  7. START_SORTER.bat   first run: air closed, chute empty -> EJECTOR TEST PANEL

Folders
  app\               the sorter program (start with START_SORTER.bat)
  cnn\               CNN code, trained models, RUN_CNN_TRAIN.bat (CPU). GPU later: UPGRADE_TO_GPU.bat
  firmware\          ESP32 firmware: ejector v2.3 (any COM port: auto detect), relay v2.0, ROLLBACK_OLD\
  ejector_test_kit\  standalone 63-valve test panel (sorter closed) - see its README
  tools\             camera probe/restore, ball speed, latency report, disk speed
  drivers\           installers: MindVision SDK, CP210x, Arduino IDE, Python 3.12
  VERSION.txt        git commit of this package     MANIFEST.sha256  checksum of every file
=====================================================================
