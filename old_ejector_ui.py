#!/usr/bin/env python3

import tkinter as tk
from tkinter import ttk, messagebox
import serial
import serial.tools.list_ports
import threading


NUM_CHANNELS = 63


class EjectorGUI:

    def __init__(self, root):

        self.root = root

        self.root.title(
            "TCM VISION - 63 Channel Matrix Ejector"
        )

        self.root.geometry(
            "1100x850"
        )

        self.ser = None
        self.connected = False
        self.reader_running = False

        self.create_ui()

        self.refresh_ports()

        self.root.protocol(
            "WM_DELETE_WINDOW",
            self.close
        )


    # =================================================
    # UI
    # =================================================

    def create_ui(self):

        # ---------------------------------------------
        # CONNECTION
        # ---------------------------------------------

        connection = ttk.LabelFrame(
            self.root,
            text="ESP32 USB Connection"
        )

        connection.pack(
            fill="x",
            padx=10,
            pady=8
        )

        ttk.Label(
            connection,
            text="Port:"
        ).pack(
            side="left",
            padx=5
        )

        self.port_combo = ttk.Combobox(
            connection,
            width=18,
            state="readonly"
        )

        self.port_combo.pack(
            side="left",
            padx=5
        )

        ttk.Button(
            connection,
            text="Refresh",
            command=self.refresh_ports
        ).pack(
            side="left",
            padx=5
        )

        self.connect_btn = ttk.Button(
            connection,
            text="CONNECT",
            command=self.toggle_connection
        )

        self.connect_btn.pack(
            side="left",
            padx=5
        )

        self.status = tk.Label(
            connection,
            text="DISCONNECTED",
            bg="red",
            fg="white",
            width=16
        )

        self.status.pack(
            side="left",
            padx=15
        )


        # ---------------------------------------------
        # TIMING
        # ---------------------------------------------

        timing = ttk.LabelFrame(
            self.root,
            text="Ejector Timing"
        )

        timing.pack(
            fill="x",
            padx=10,
            pady=5
        )

        ttk.Label(
            timing,
            text="ON Time (ms):"
        ).grid(
            row=0,
            column=0,
            padx=5,
            pady=8
        )

        self.on_time = ttk.Entry(
            timing,
            width=10
        )

        self.on_time.insert(
            0,
            "1"
        )

        self.on_time.grid(
            row=0,
            column=1
        )


        ttk.Label(
            timing,
            text="Pulse Gap (ms):"
        ).grid(
            row=0,
            column=2,
            padx=5
        )

        self.gap_time = ttk.Entry(
            timing,
            width=10
        )

        self.gap_time.insert(
            0,
            "5"
        )

        self.gap_time.grid(
            row=0,
            column=3
        )


        ttk.Label(
            timing,
            text="Start Delay (ms):"
        ).grid(
            row=0,
            column=4,
            padx=5
        )

        self.start_delay = ttk.Entry(
            timing,
            width=10
        )

        self.start_delay.insert(
            0,
            "0"
        )

        self.start_delay.grid(
            row=0,
            column=5
        )


        # ---------------------------------------------
        # MANUAL TEST
        # ---------------------------------------------

        manual = ttk.LabelFrame(
            self.root,
            text="MANUAL CHANNEL TEST"
        )

        manual.pack(
            fill="x",
            padx=10,
            pady=8
        )

        grid = ttk.Frame(
            manual
        )

        grid.pack(
            padx=10,
            pady=10
        )

        columns = 9

        for ch in range(
            1,
            NUM_CHANNELS + 1
        ):

            button = tk.Button(
                grid,
                text=f"CH {ch:02d}",
                width=9,
                height=2,
                command=lambda c=ch:
                    self.fire_once(c)
            )

            row = (ch - 1) // columns
            col = (ch - 1) % columns

            button.grid(
                row=row,
                column=col,
                padx=3,
                pady=3
            )


        # ---------------------------------------------
        # CONTINUOUS
        # ---------------------------------------------

        continuous = ttk.LabelFrame(
            self.root,
            text="CONTINUOUS FIRE TEST"
        )

        continuous.pack(
            fill="x",
            padx=10,
            pady=8
        )

        ttk.Label(
            continuous,
            text="Channel:"
        ).grid(
            row=0,
            column=0,
            padx=5,
            pady=8
        )

        self.cont_channel = ttk.Entry(
            continuous,
            width=8
        )

        self.cont_channel.insert(
            0,
            "1"
        )

        self.cont_channel.grid(
            row=0,
            column=1
        )

        ttk.Button(
            continuous,
            text="START CONTINUOUS",
            command=self.start_continuous
        ).grid(
            row=0,
            column=2,
            padx=10
        )

        ttk.Button(
            continuous,
            text="STOP",
            command=self.stop
        ).grid(
            row=0,
            column=3,
            padx=10
        )


        # ---------------------------------------------
        # AUTO 1-63
        # ---------------------------------------------

        auto = ttk.LabelFrame(
            self.root,
            text="AUTO SEQUENCE"
        )

        auto.pack(
            fill="x",
            padx=10,
            pady=8
        )

        ttk.Label(
            auto,
            text="Start:"
        ).grid(
            row=0,
            column=0,
            padx=5
        )

        self.auto_start = ttk.Entry(
            auto,
            width=8
        )

        self.auto_start.insert(
            0,
            "1"
        )

        self.auto_start.grid(
            row=0,
            column=1
        )

        ttk.Label(
            auto,
            text="End:"
        ).grid(
            row=0,
            column=2,
            padx=5
        )

        self.auto_end = ttk.Entry(
            auto,
            width=8
        )

        self.auto_end.insert(
            0,
            "63"
        )

        self.auto_end.grid(
            row=0,
            column=3
        )

        ttk.Button(
            auto,
            text="START 1 → 63",
            command=self.start_auto
        ).grid(
            row=0,
            column=4,
            padx=15
        )


        # ---------------------------------------------
        # CURRENT STATUS
        # ---------------------------------------------

        self.current = tk.Label(
            self.root,
            text="READY",
            font=(
                "Arial",
                18,
                "bold"
            )
        )

        self.current.pack(
            pady=8
        )


        # ---------------------------------------------
        # ALL OFF
        # ---------------------------------------------

        tk.Button(
            self.root,
            text="ALL OUTPUTS OFF",
            command=self.all_off,
            width=25,
            height=2
        ).pack(
            pady=5
        )


        # ---------------------------------------------
        # LOG
        # ---------------------------------------------

        log_frame = ttk.LabelFrame(
            self.root,
            text="ESP32 Communication"
        )

        log_frame.pack(
            fill="both",
            expand=True,
            padx=10,
            pady=8
        )

        self.log = tk.Text(
            log_frame,
            height=8
        )

        self.log.pack(
            fill="both",
            expand=True
        )


    # =================================================
    # LOG
    # =================================================

    def log_msg(self, msg):

        self.log.insert(
            "end",
            msg + "\n"
        )

        self.log.see("end")


    # =================================================
    # PORTS
    # =================================================

    def refresh_ports(self):

        ports = [
            p.device
            for p in serial.tools.list_ports.comports()
        ]

        self.port_combo["values"] = ports

        if ports:
            self.port_combo.current(0)


    # =================================================
    # CONNECT
    # =================================================

    def toggle_connection(self):

        if self.connected:

            self.disconnect()

        else:

            self.connect()


    def connect(self):

        port = self.port_combo.get()

        if not port:

            messagebox.showerror(
                "ESP32",
                "USB port select செய்யவும்."
            )

            return

        try:

            self.ser = serial.Serial(
                port,
                115200,
                timeout=0.1
            )

            self.connected = True

            self.connect_btn.config(
                text="DISCONNECT"
            )

            self.status.config(
                text="CONNECTED",
                bg="green"
            )

            self.log_msg(
                "CONNECTED: " + port
            )

            self.reader_running = True

            threading.Thread(
                target=self.reader,
                daemon=True
            ).start()

        except Exception as e:

            messagebox.showerror(
                "Connection Error",
                str(e)
            )


    def disconnect(self):

        self.connected = False
        self.reader_running = False

        try:

            if self.ser:
                self.ser.close()

        except:
            pass

        self.ser = None

        self.connect_btn.config(
            text="CONNECT"
        )

        self.status.config(
            text="DISCONNECTED",
            bg="red"
        )


    # =================================================
    # SERIAL READER
    # =================================================

    def reader(self):

        while self.reader_running:

            try:

                if (
                    self.ser and
                    self.ser.in_waiting
                ):

                    line = (
                        self.ser
                        .readline()
                        .decode(
                            errors="ignore"
                        )
                        .strip()
                    )

                    if line:

                        self.root.after(
                            0,
                            self.received,
                            line
                        )

            except Exception:

                break


    def received(self, line):

        self.log_msg(
            "ESP32: " + line
        )

        if line.startswith(
            "AUTO_CHANNEL,"
        ):

            ch = line.split(
                ","
            )[1]

            self.current.config(
                text=f"AUTO → CH {ch}"
            )

        elif line.startswith(
            "FIRED,"
        ):

            ch = line.split(
                ","
            )[1]

            self.current.config(
                text=f"FIRED → CH {ch}"
            )

        elif line == "CONT_STARTED":

            self.current.config(
                text="CONTINUOUS RUNNING"
            )

        elif line == "CONT_STOPPED":

            self.current.config(
                text="CONTINUOUS STOPPED"
            )

        elif line == "AUTO_FINISHED":

            self.current.config(
                text="AUTO TEST FINISHED"
            )

        elif line == "STOPPED":

            self.current.config(
                text="STOPPED"
            )


    # =================================================
    # SEND
    # =================================================

    def send(self, command):

        if not self.connected:

            messagebox.showwarning(
                "ESP32",
                "ESP32 CONNECT செய்யவும்."
            )

            return False

        try:

            self.ser.write(
                (
                    command + "\n"
                ).encode()
            )

            self.log_msg(
                "PC: " + command
            )

            return True

        except Exception as e:

            self.log_msg(
                "ERROR: " + str(e)
            )

            return False


    # =================================================
    # GET VALUES
    # =================================================

    def get_value(
        self,
        widget,
        default=1
    ):

        try:

            value = int(
                widget.get()
            )

            if value < 0:
                return default

            return value

        except:

            return default


    # =================================================
    # MANUAL FIRE
    # =================================================

    def fire_once(self, channel):

        on_time = self.get_value(
            self.on_time,
            1
        )

        command = (
            f"FIRE,"
            f"{channel},"
            f"{on_time}"
        )

        if self.send(command):

            self.current.config(
                text=f"FIRING CH {channel}"
            )


    # =================================================
    # CONTINUOUS
    # =================================================

    def start_continuous(self):

        channel = self.get_value(
            self.cont_channel,
            1
        )

        if channel < 1 or channel > 63:

            messagebox.showerror(
                "Error",
                "Channel 1 முதல் 63 வரை."
            )

            return

        on_time = self.get_value(
            self.on_time,
            1
        )

        gap = self.get_value(
            self.gap_time,
            5
        )

        delay = self.get_value(
            self.start_delay,
            0
        )

        command = (
            f"CONT_START,"
            f"{channel},"
            f"{on_time},"
            f"{gap},"
            f"{delay}"
        )

        self.send(command)


    # =================================================
    # AUTO
    # =================================================

    def start_auto(self):

        start = self.get_value(
            self.auto_start,
            1
        )

        end = self.get_value(
            self.auto_end,
            63
        )

        on_time = self.get_value(
            self.on_time,
            1
        )

        gap = self.get_value(
            self.gap_time,
            5
        )

        if (
            start < 1 or
            end > 63 or
            start > end
        ):

            messagebox.showerror(
                "Error",
                "Auto range 1 முதல் 63."
            )

            return

        command = (
            f"AUTO,"
            f"{start},"
            f"{end},"
            f"{on_time},"
            f"{gap}"
        )

        self.send(command)


    # =================================================
    # STOP
    # =================================================

    def stop(self):

        self.send(
            "STOP"
        )

        self.current.config(
            text="STOP"
        )


    # =================================================
    # ALL OFF
    # =================================================

    def all_off(self):

        self.send(
            "ALL_OFF"
        )

        self.current.config(
            text="ALL OUTPUTS OFF"
        )


    # =================================================
    # CLOSE
    # =================================================

    def close(self):

        try:

            if self.connected:

                self.send(
                    "ALL_OFF"
                )

        except:
            pass

        self.disconnect()

        self.root.destroy()


# =====================================================
# MAIN
# =====================================================

if __name__ == "__main__":

    root = tk.Tk()

    app = EjectorGUI(
        root
    )

    root.mainloop()
