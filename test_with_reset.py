import serial, time, asyncio, threading
from pymodbus.simulator.simdata import SimData, DataType
from pymodbus.simulator.simdevice import SimDevice
from pymodbus.server import ModbusSerialServer

PORT = "COM13"
BAUD = 115200

# STEP 1: Reset ESP32 first
print("[STEP 1] Resetting ESP32 via DTR toggle...")
s = serial.Serial(PORT, BAUD, timeout=0.5)
s.dtr = True
time.sleep(0.1)
s.dtr = False
time.sleep(0.1)
s.close()
print("  ESP32 reset done. Waiting 4 sec for boot...")
time.sleep(4)

# STEP 2: Start Modbus Slave Server
print("[STEP 2] Starting Modbus Slave Server...")
init_vals = [0] * 16
hr_simdata = SimData(address=0, values=init_vals, datatype=DataType.REGISTERS)
di = SimData(address=0, values=[False]*16, datatype=DataType.BITS)
co = SimData(address=0, values=[False]*16, datatype=DataType.BITS)
ir = SimData(address=0, values=[0]*16, datatype=DataType.REGISTERS)
device = SimDevice(id=1, simdata=([di], [co], [hr_simdata], [ir]))

server = None
poll_count = [0]
server_ready = threading.Event()

def trace_packet(is_rx, packet):
    if is_rx:
        poll_count[0] += 1
        if poll_count[0] <= 3 or poll_count[0] % 100 == 0:
            print(f"  [POLL #{poll_count[0]}] ESP32 reading registers!")
    return packet

async def run_server():
    global server
    server = ModbusSerialServer(
        context=device, port=PORT, baudrate=BAUD,
        stopbits=1, bytesize=8, parity='N', framer='rtu',
        trace_packet=trace_packet
    )
    sim_rt = server.context.devices[1]
    hr = sim_rt.block['h'][2]
    print(f"  Server started! HR: {list(hr[:8])}")
    server_ready.set()
    await server.serve_forever()

t = threading.Thread(target=lambda: asyncio.run(run_server()), daemon=True)
t.start()

if not server_ready.wait(timeout=5.0):
    print("[FAIL] Server didn't start!")
    exit(1)

print("[STEP 3] Waiting 5 sec for ESP32 polls...")
time.sleep(5)

print(f"\n  Total polls: {poll_count[0]}")

if poll_count[0] > 0:
    sim_rt = server.context.devices[1]
    hr = sim_rt.block['h'][2]
    
    print("\n[TEST 1] Relay 4 (LED) ON...")
    hr[3] = 1
    time.sleep(3)
    print(f"  HR: {list(hr[:8])}")
    
    print("\n[TEST 2] ALL RELAYS ON...")
    for i in range(8):
        hr[i] = 1
    print(f"  HR: {list(hr[:8])}")
    time.sleep(5)
    
    print("\n[TEST 3] ALL OFF...")
    for i in range(8):
        hr[i] = 0
    time.sleep(2)
    
    print(f"\n[SUCCESS] Total polls: {poll_count[0]}")
    print("ESP32 Modbus communication WORKING!")
else:
    print("[FAIL] No polls received")
