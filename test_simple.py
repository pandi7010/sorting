import time, asyncio, threading
from pymodbus.simulator.simdata import SimData, DataType
from pymodbus.simulator.simdevice import SimDevice
from pymodbus.server import ModbusSerialServer

PORT = "COM13"
BAUDRATE = 115200

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
        if poll_count[0] <= 5 or poll_count[0] % 50 == 0:
            print(f"  [RX #{poll_count[0]}] ESP32 polling! Packet len={len(packet)}")
    return packet

async def run_server():
    global server
    server = ModbusSerialServer(
        context=device, port=PORT, baudrate=BAUDRATE,
        stopbits=1, bytesize=8, parity='N', framer='rtu',
        trace_packet=trace_packet
    )
    sim_rt = server.context.devices[1]
    hr_ref = sim_rt.block['h'][2]
    print(f"[OK] Modbus Server STARTED on {PORT} @ {BAUDRATE}")
    print(f"  HR registers: {list(hr_ref[:8])}")
    server_ready.set()
    await server.serve_forever()

def server_thread():
    asyncio.run(run_server())

t = threading.Thread(target=server_thread, daemon=True)
t.start()

if not server_ready.wait(timeout=5.0):
    print("[FAIL] Server did not start!")
    exit(1)

print(f"\nWaiting 8 seconds for ESP32 polls...")
time.sleep(8)

print(f"\nPolls received: {poll_count[0]}")
if poll_count[0] > 0:
    print("[SUCCESS] ESP32 is communicating!")
    sim_rt = server.context.devices[1]
    hr_ref = sim_rt.block['h'][2]
    
    print("\n>>> TEST: Relay 4 ON (LED)...")
    hr_ref[3] = 1
    time.sleep(3)
    
    print(">>> TEST: ALL RELAYS ON...")
    for i in range(8):
        hr_ref[i] = 1
    print(f"  HR: {list(hr_ref[:8])}")
    time.sleep(5)
    
    print(">>> ALL OFF...")
    for i in range(8):
        hr_ref[i] = 0
    time.sleep(2)
    print(f"[DONE] Total polls: {poll_count[0]}")
else:
    print("[FAIL] ESP32 NOT communicating. Check USB cable and uploaded code.")
