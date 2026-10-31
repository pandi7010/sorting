import sys
import os

filepath = 'd:/LAMTON_V1/LAMTON_LINE_NEW_ESP32.py'
try:
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # 1. Fix connection logic and initialization
    old_conn = '''                init_vals = [0, 0, 0, r4, r5, r6, r7, r8] + [0]*8
                from pymodbus.simulator.simdata import SimData, DataType
                from pymodbus.simulator.simdevice import SimDevice'''
    
    new_conn = '''                self.server_ready_event.clear()
                init_vals = [0]*16
                from pymodbus.simulator.simdata import SimData, DataType
                from pymodbus.simulator.simdevice import SimDevice'''
                
    content = content.replace(old_conn, new_conn)

    # 2. Fix _run_modbus_server assignment
    old_run = '''                sim_runtime = self.server.context.devices[1]
                self.hr_registers = sim_runtime.block['h'][2]
                print(f"[DEBUG] ESP32 Modbus HR registers active: {self.hr_registers[:8]}")
                self.server_ready_event.set()

                await self.server.serve_forever()
            except Exception as e:
                print(f"[INFO] Modbus Server on {port} inactive: {e}")
                self.server_ready_event.set()
                self.running = False
                self.connected = False

        asyncio.run(run_async_server())'''
        
    new_run = '''                sim_runtime = self.server.context.devices[1]
                self.hr_registers = sim_runtime.block['h'][2]
                print(f"[DEBUG] ESP32 Modbus HR registers active: {self.hr_registers[:8]}")
                self.server_ready_event.set()

                await self.server.serve_forever()
            except Exception as e:
                print(f"[INFO] Modbus Server on {port} inactive: {e}")
                self.server = None
                self.server_ready_event.set()
                self.running = False
                self.connected = False

        try:
            asyncio.run(run_async_server())
        except Exception as e:
            print(f"[ERROR] run_async_server fatal: {e}")'''
            
    content = content.replace(old_run, new_run)

    # 3. Fix disconnect clearing event
    old_disc = '''            self.hr_registers = None
            self.modbus_thread = None
            time.sleep(0.15)'''
            
    new_disc = '''            self.hr_registers = None
            self.modbus_thread = None
            self.server_ready_event.clear()
            time.sleep(0.15)'''
            
    content = content.replace(old_disc, new_disc)

    # 4. Wait for ready safely
    old_wait = '''                ready = self.server_ready_event.wait(timeout=3.0)
                if ready and self.hr_registers is not None:'''
                
    new_wait = '''                ready = self.server_ready_event.wait(timeout=3.0)
                if ready and self.server is not None and self.hr_registers is not None:'''
                
    content = content.replace(old_wait, new_wait)
    
    # 5. Fix feeder sequence delay
    old_seq1 = '''            pwm = int(dial_val * 2.55)
            if hasattr(self, 'esp32') and self.esp32 and self.esp32.connected:
                self.esp32.set_vibrator(1, pwm)
                self.esp32.set_ejector(1)
        else:'''
        
    new_seq1 = '''            pwm = int(dial_val * 2.55)
            if hasattr(self, 'esp32') and self.esp32 and self.esp32.connected:
                import threading
                self.esp32.set_ejector(1)
                threading.Timer(1.0, lambda: self.esp32.set_vibrator(1, pwm)).start()
        else:'''
        
    content = content.replace(old_seq1, new_seq1)
    
    # 6. Fix wiper auto-restart sequence
    old_seq2 = '''    def restart_feeder_after_wipe(self, previous_pwm):
        if hasattr(self, 'esp32') and self.esp32.connected:
            # Ejector first, wait 1 sec, then Vibrator
            self.esp32.set_ejector(1)
            print("Wiper finished. Ejector ON. Restarting feeder in 1s...")
            self.feeder_data[0]['active'] = True
            self.update_feeder_toggle_appearance()
            threading.Timer(1.0, lambda: self.esp32.set_vibrator(1, previous_pwm)).start()'''
            
    new_seq2 = '''    def restart_feeder_after_wipe(self, previous_pwm):
        if hasattr(self, 'esp32') and self.esp32.connected:
            import threading
            print("Wiper completed. Waiting 2 seconds before Ejector and Vibrator restart...")
            def _sequence():
                if hasattr(self, 'esp32') and self.esp32.connected:
                    self.esp32.set_ejector(1)
                    self.esp32.set_vibrator(1, previous_pwm)
                    self.update_feeder_toggle_appearance()
            self.feeder_data[0]['active'] = True
            threading.Timer(2.0, _sequence).start()'''
            
    content = content.replace(old_seq2, new_seq2)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    
    print('PATCH SUCCESS')

except Exception as e:
    print('ERROR:', e)
