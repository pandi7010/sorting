# MAIN_LAUNCHER.py
# Starts the sorter (LAMTON_LINE_NEW_ESP32.py) from its own folder.
# (The old version imported a missing module "main1" and crashed.)
import os
import runpy

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    os.chdir(HERE)
    runpy.run_path(os.path.join(HERE, "LAMTON_LINE_NEW_ESP32.py"), run_name="__main__")


if __name__ == "__main__":
    main()
