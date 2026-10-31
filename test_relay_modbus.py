"""
ESP32 8-Channel Relay Board - Quick Modbus Test
Port: COM13 | Baud: 115200
Test: Turn ON relays one by one to verify communication
"""
import time, asyncio, threading, sys

def test_modbus(start_address=0):
    try:
        from pymodbus.simulator.simdata import SimData, DataType
        from pymodbus.simulator.simdevice import SimDevice
        from pymodbus.server import ModbusSerialServer
    except ImportError:
        print("ERROR: pymodbus not installed")
        sys.exit(1)

    PORT = "COM13"
    BAUDRATE = 115200
    
    print(f"{'='*60}")
    print(f"  ESP32 8-CH RELAY MODBUS TEST")
    print(f"  Port: {PORT} | Baud: {BAUDRATE} | SimData Address: {start_address}")
    print(f"{'='*60}")
    
    init_vals = [0] * 16
    hr_simdata = SimData(address=start_address, values=init_vals, datatype=DataType.REGISTERS)
    di = SimData(address=start_address, values=[False]*16, datatype=DataType.BITS)
    co = SimData(address=start_address, values=[False]*16, datatype=DataType.BITS)
    ir = SimData(address=start_address, values=[0]*16, datatype=DataType.REGISTERS)
    device = SimDevice(id=1, simdata=([di], [co], [hr_simdata], [ir]))
    
    hr_registers = hr_simdata.values
    server = None
    server_ready = threading.Event()
    poll_count = [0]
    
    def trace_packet(is_rx, packet):
        if is_rx:
            poll_count[0] += 1
            if poll_count[0] % 20 == 1:
                print(f"  [RX #{poll_count[0]}] ESP32 polling! HR: {list(hr_registers[:8])}")
        return packet
    
    async def run_server():
        nonlocal server
        try:
            server = ModbusSerialServer(
                context=device, port=PORT, baudrate=BAUDRATE,
                stopbits=1, bytesize=8, parity='N', framer='rtu',
                trace_packet=trace_packet
            )
            sim_rt = server.context.devices[1]
            hr_ref = sim_rt.block['h'][2]
            print(f"  [OK] Server started. HR block: {hr_ref[:8]}")
            server_ready.set()
            await server.serve_forever()
        except Exception as e:
            print(f"  [ERROR] Server: {e}")
            server_ready.set()
    
    t = threading.Thread(target=lambda: asyncio.run(run_server()), daemon=True)
    t.start()
    
    if not server_ready.wait(timeout=5.0):
        print("  [FAIL] Server did not start!")
        return False
    
    print(f"\n  Waiting 5 sec for ESP32 to start polling...")
    time.sleep(5)
    
    if poll_count[0] == 0:
        print(f"  [FAIL] No polls from ESP32 with address={start_address}!")
        return False
    
    print(f"  [OK] ESP32 IS POLLING! ({poll_count[0]} polls)")
    
    sim_rt = server.context.devices[1]
    hr_ref = sim_rt.block['h'][2]
    
    print(f"\n  >>> Turning ON Relay 4 (LED)...")
    hr_ref[3] = 1
    print(f"  HR: {list(hr_ref[:8])}")
    print(f"  CHECK: Does Relay 4 LED turn ON?")
    time.sleep(3)
    
    print(f"\n  >>> Turning ON ALL 8 Relays...")
    for i in range(8):
        hr_ref[i] = 1
    print(f"  HR: {list(hr_ref[:8])}")
    print(f"  CHECK: Do ALL relays click ON?")
    time.sleep(5)
    
    print(f"\n  >>> Turning OFF ALL...")
    for i in range(8):
        hr_ref[i] = 0
    time.sleep(2)
    
    print(f"\n  [DONE] Total polls: {poll_count[0]}")
    return True

if __name__ == "__main__":
    print("\nTEST 1: Address 0 (matching Arduino START_REG=0x0000)")
    result = test_modbus(start_address=0)
    if not result:
        print("\nAddress 0 failed. TEST 2: Address 1...")
        time.sleep(2)
        test_modbus(start_address=1)
