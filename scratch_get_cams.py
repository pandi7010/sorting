import sys
sys.path.append("d:/LAMTON_V1")
import mvsdk

def check_cameras():
    try:
        DevList = mvsdk.CameraEnumerateDevice()
        nDev = len(DevList)
        if nDev < 1:
            print("No cameras found.")
            return

        print(f"Found {nDev} cameras:")
        for i, DevInfo in enumerate(DevList):
            sn = DevInfo.GetSn()
            name = DevInfo.GetFriendlyName()
            mac = DevInfo.GetPortType() # Usually gigE mac is somewhere here or we just use SN
            print(f"Camera {i+1}: Name='{name}', Serial='{sn}'")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_cameras()
