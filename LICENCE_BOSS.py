import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
import sqlite3
import hashlib
import datetime
import os

# ==========================================
# ⚙️ CONFIGURATION
# ==========================================
MANAGER_PASS = "1234"
BOSS_PASS = "4321"

BASE_DATE = datetime.date(1991, 11, 11)
SECRET_KEY = "LAMBTON_MASTER_KEY"
# Version 3 creates a new DB automatically with 'Location' support
DB_FILE = "manufacturer_data_v3.db"


# ==========================================
# 🗄️ DATABASE SETUP
# ==========================================
def setup_database():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # கவனமாக இந்த SQL-ஐ பாருங்கள் - இதில் புள்ளி (dot) அல்லது ... கிடையாது
    query = '''
    CREATE TABLE IF NOT EXISTS machines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        serial_no TEXT UNIQUE,
        pc_id TEXT,
        customer_name TEXT,
        location TEXT,
        payment_status TEXT,
        last_password_date TEXT,
        expiry_days INTEGER
    )
    '''
    cursor.execute(query)
    conn.commit()
    conn.close()

def add_new_machine(serial, mfg_date, payment, cust_name, location):
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO machines (serial_no, mfg_date, payment_status, customer_name, location, last_password_date, expiry_days) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (serial, mfg_date, payment, cust_name, location, "New", 0)
        )
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        return False


def get_machine_details(serial_no):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM machines WHERE serial_no=?", (serial_no,))
    data = cursor.fetchone()
    conn.close()
    return data


def update_history(serial_no, days):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    cursor.execute("UPDATE machines SET last_password_date=?, expiry_days=? WHERE serial_no=?", (now, days, serial_no))
    conn.commit()
    conn.close()


def delete_machine_logic(serial_no):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM machines WHERE serial_no=?", (serial_no,))
    conn.commit()
    conn.close()


def update_machine_details(original_serial, new_cust, new_loc, new_pay):
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("UPDATE machines SET customer_name=?, location=?, payment_status=? WHERE serial_no=?",
                   (new_cust, new_loc, new_pay, original_serial))
    conn.commit()
    conn.close()


# ==========================================
# 🗄️ DATABASE SETUP
# ==========================================
def setup_database():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # கவனமாக இந்த SQL-ஐ பாருங்கள் - இதில் புள்ளி (dot) அல்லது ... கிடையாது
    query = '''
    CREATE TABLE IF NOT EXISTS machines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        serial_no TEXT UNIQUE,
        pc_id TEXT,
        customer_name TEXT,
        location TEXT,
        payment_status TEXT,
        last_password_date TEXT,
        expiry_days INTEGER
    )
    '''
    cursor.execute(query)
    conn.commit()
    conn.close()


# கீ ஜெனரேஷன் லாஜிக் (இங்கேதான் 8BE3 போன்ற முதல் 4 எழுத்துக்கள் சேரும்)
def generate_password_logic(pc_id, days):
    # ஹார்டுவேர் ஐடியின் முதல் 4 எழுத்துக்கள்
    hw_prefix = pc_id.replace("-", "")[:4]

    # காலாவதி தேதி கணக்கீடு
    expiry_date = datetime.date.today() + datetime.timedelta(days=days)
    days_hex = f"{(expiry_date - BASE_DATE).days:04X}"

    # சிக்னேச்சர்: HWID + Days + SECRET_KEY
    raw_str = f"{pc_id.replace('-', '')}{days_hex}{SECRET_KEY}"
    signature = hashlib.sha256(raw_str.encode()).hexdigest().upper()[:8]

    # 16-இலக்க கீ: [4 HWID] + [4 DAYS] + [8 SIGNATURE]
    return f"{hw_prefix}{days_hex}{signature}"

# ==========================================
# 🖥️ MAIN APPLICATION
# ==========================================
class MasterControlApp:
    def __init__(self, root):
        self.root = root
        self.root.title("CAPTAIN AI - MASTER CONTROL STUDIO V3")
        self.root.geometry("1250x850")
        self.root.configure(bg="#212121")
        self.current_user_level = None
        setup_database()
        self.show_login_screen()

    # --- SCREEN 1: LOGIN ---
    def show_login_screen(self):
        self.clear_window()
        frame = tk.Frame(self.root, bg="#212121")
        frame.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(frame, text="🛡️ SYSTEM LOGIN", font=("Arial", 20, "bold"), bg="#212121", fg="white").pack(pady=20)
        tk.Label(frame, text="Enter Passcode:", bg="#212121", fg="gray").pack()
        self.entry_pass = tk.Entry(frame, show="*", font=("Arial", 16), justify='center')
        self.entry_pass.pack(pady=10)
        self.entry_pass.bind('<Return>', lambda e: self.check_login())
        tk.Button(frame, text="LOGIN", bg="#00E676", font=("Arial", 12, "bold"), width=15,
                  command=self.check_login).pack(pady=20)

    def check_login(self):
        pwd = self.entry_pass.get()
        if pwd == MANAGER_PASS:
            self.current_user_level = "MANAGER"
            self.show_dashboard()
        elif pwd == BOSS_PASS:
            self.current_user_level = "BOSS"
            self.show_dashboard()
        else:
            messagebox.showerror("Error", "Wrong Password")

    # --- SCREEN 2: DASHBOARD ---
    def show_dashboard(self):
        self.clear_window()

        # Header
        bg_color = "#D50000" if self.current_user_level == "BOSS" else "#2962FF"
        mode_text = "BOSS MODE (FULL ACCESS)" if self.current_user_level == "BOSS" else "MANAGER MODE"
        header = tk.Frame(self.root, bg=bg_color, height=50)
        header.pack(fill="x")
        tk.Label(header, text=mode_text, font=("Arial", 14, "bold"), bg=bg_color, fg="white").pack(side="left", padx=20)
        tk.Button(header, text="LOGOUT", bg="white", fg="red", font=("Arial", 9, "bold"),
                  command=self.show_login_screen).pack(side="right", padx=20)

        # === LEFT PANEL ===
        left_frame = tk.Frame(self.root, width=500, bg="#303030")
        left_frame.pack(side="left", fill="both", padx=10, pady=10)

        tab_control = ttk.Notebook(left_frame)
        self.tab_search = tk.Frame(tab_control, bg="#424242")
        tab_control.add(self.tab_search, text=' 🔍 Search ')

        if self.current_user_level == "BOSS":
            self.tab_register = tk.Frame(tab_control, bg="#424242")
            tab_control.add(self.tab_register, text=' ➕ Register ')
        tab_control.pack(expand=0, fill="x")

        # -- Search Tab --
        tk.Label(self.tab_search, text="Enter Machine ID:", font=("Arial", 12), bg="#424242", fg="white").pack(
            pady=(10, 5))
        self.search_var = tk.StringVar()
        self.entry_search = tk.Entry(self.tab_search, textvariable=self.search_var, font=("Arial", 16))
        self.entry_search.pack(pady=5, padx=20, fill="x")
        self.entry_search.bind("<FocusIn>", lambda e: self.set_focus(self.entry_search))
        tk.Button(self.tab_search, text="SEARCH", bg="#2962FF", fg="white", font=("Arial", 11, "bold"),
                  command=self.perform_search).pack(pady=10, padx=20, fill="x")

        # -- Register Tab --
        if self.current_user_level == "BOSS":
            lbl_font = ("Arial", 10, "bold")
            # Serial
            tk.Label(self.tab_register, text="Serial No:", bg="#424242", fg="#00E676", font=lbl_font).pack(anchor="w",
                                                                                                           padx=20,
                                                                                                           pady=(10, 0))
            self.ent_new_serial = tk.Entry(self.tab_register, font=("Arial", 12))
            self.ent_new_serial.pack(padx=20, fill="x")
            self.ent_new_serial.bind("<FocusIn>", lambda e: self.set_focus(self.ent_new_serial))

            # Name
            tk.Label(self.tab_register, text="Customer Name:", bg="#424242", fg="white", font=lbl_font).pack(anchor="w",
                                                                                                             padx=20,
                                                                                                             pady=(5,
                                                                                                                   0))
            self.ent_new_cust = tk.Entry(self.tab_register, font=("Arial", 12))
            self.ent_new_cust.pack(padx=20, fill="x")
            self.ent_new_cust.bind("<FocusIn>", lambda e: self.set_focus(self.ent_new_cust))

            # Location (NEW)
            tk.Label(self.tab_register, text="Location:", bg="#424242", fg="white", font=lbl_font).pack(anchor="w",
                                                                                                        padx=20,
                                                                                                        pady=(5, 0))
            self.ent_new_loc = tk.Entry(self.tab_register, font=("Arial", 12))
            self.ent_new_loc.pack(padx=20, fill="x")
            self.ent_new_loc.bind("<FocusIn>", lambda e: self.set_focus(self.ent_new_loc))

            # Payment
            tk.Label(self.tab_register, text="Payment:", bg="#424242", fg="white", font=lbl_font).pack(anchor="w",
                                                                                                       padx=20,
                                                                                                       pady=(5, 0))
            self.combo_payment = ttk.Combobox(self.tab_register, values=["Paid", "Pending", "Advance"],
                                              font=("Arial", 12))
            self.combo_payment.pack(padx=20, fill="x")

            tk.Button(self.tab_register, text="SAVE MACHINE", bg="#00C853", fg="white", font=("Arial", 11, "bold"),
                      command=self.save_new_machine).pack(pady=15, fill="x", padx=20)

        # === KEYBOARD ===
        kb_frame = tk.Frame(left_frame, bg="#212121", bd=2, relief="sunken")
        kb_frame.pack(side="bottom", fill="both", expand=True, pady=10)
        self.active_entry = self.entry_search
        self.create_keyboard(kb_frame)

        # === RIGHT PANEL ===
        right_frame = tk.Frame(self.root, bg="#212121")
        right_frame.pack(side="right", expand=True, fill="both", padx=10, pady=10)

        self.lbl_info = tk.Label(right_frame, text="Select a machine...", font=("Arial", 14), bg="#212121", fg="gray")
        self.lbl_info.pack(pady=10)

        # --- CONTROLS ---
        ctrl_frame = tk.Frame(right_frame, bg="#212121")
        ctrl_frame.pack(pady=5, fill="x")

        # Password Buttons (Left)
        pass_frame = tk.Frame(ctrl_frame, bg="#212121")
        pass_frame.pack(side="left")
        for d in [1, 7, 30, 60]:
            tk.Button(pass_frame, text=f"{d} Days", width=7, bg="#90CAF9",
                      command=lambda x=d: self.generate_key(x)).pack(side="left", padx=2)

        if self.current_user_level == "BOSS":
            tk.Button(pass_frame, text="LIFETIME", width=8, bg="red", fg="white",
                      command=lambda: self.generate_key(99999)).pack(side="left", padx=10)

        # BOSS EDIT/DELETE Buttons (Right)
        if self.current_user_level == "BOSS":
            admin_frame = tk.Frame(ctrl_frame, bg="#212121")
            admin_frame.pack(side="right")
            tk.Button(admin_frame, text="✏️ EDIT", bg="#FF9800", fg="black", font=("Arial", 10, "bold"),
                      command=self.edit_machine_popup).pack(side="left", padx=5)
            tk.Button(admin_frame, text="🗑️ DELETE", bg="#B71C1C", fg="white", font=("Arial", 10, "bold"),
                      command=self.delete_machine_action).pack(side="left", padx=5)

        # --- TABLE ---
        # Added "Location" Column
        columns = ("Serial", "Customer", "Location", "Payment", "Last Issued", "Days")
        self.tree = ttk.Treeview(right_frame, columns=columns, show='headings', height=15)

        self.tree.heading("Serial", text="Serial No")
        self.tree.heading("Customer", text="Customer")
        self.tree.heading("Location", text="Location")  # NEW
        self.tree.heading("Payment", text="Payment")
        self.tree.heading("Last Issued", text="Last Key")
        self.tree.heading("Days", text="Valid")

        self.tree.column("Serial", width=90)
        self.tree.column("Customer", width=130)
        self.tree.column("Location", width=100)  # NEW
        self.tree.column("Payment", width=80)
        self.tree.column("Last Issued", width=120)
        self.tree.column("Days", width=60)

        self.tree.pack(fill="both", expand=True, pady=10)

        # Bind table click to selection
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)

        self.refresh_table()

    # --- LOGIC ---
    def set_focus(self, widget):
        self.active_entry = widget

    def key_press(self, char):
        if not self.active_entry: return
        if char == "SPACE":
            self.active_entry.insert(tk.END, " ")
        elif char == "⬅":
            txt = self.active_entry.get()
            self.active_entry.delete(len(txt) - 1, tk.END)
        elif char == "CLR":
            self.active_entry.delete(0, tk.END)
        else:
            self.active_entry.insert(tk.END, char)

    def create_keyboard(self, frame):
        keys = [
            ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
            ['Q', 'W', 'E', 'R', 'T', 'Y', 'U', 'I', 'O', 'P'],
            ['A', 'S', 'D', 'F', 'G', 'H', 'J', 'K', 'L', '-'],
            ['Z', 'X', 'C', 'V', 'B', 'N', 'M', '⬅', 'CLR']
        ]
        for row in keys:
            r_fr = tk.Frame(frame, bg="#212121")
            r_fr.pack(pady=2)
            for key in row:
                w = 20 if key == "SPACE" else 4
                c = "#d32f2f" if key == "⬅" else ("#ff9800" if key == "CLR" else "#424242")
                tk.Button(r_fr, text=key, width=w, height=2, bg=c, fg="white", font=("Arial", 10, "bold"),
                          command=lambda k=key: self.key_press(k)).pack(side="left", padx=2)
        tk.Button(frame, text="SPACE", width=30, height=2, bg="#424242", fg="white",
                  command=lambda: self.key_press("SPACE")).pack(pady=5)

    def save_new_machine(self):
        s = self.ent_new_serial.get().strip()
        c = self.ent_new_cust.get().strip()
        l = self.ent_new_loc.get().strip()
        p = self.combo_payment.get()
        mfg = datetime.datetime.now().strftime("%Y-%m-%d")

        if not s or not c or not l: messagebox.showwarning("Missing", "Fill Serial, Customer and Location"); return

        if add_new_machine(s, mfg, p, c, l):
            messagebox.showinfo("Saved", f"Machine {s} Added!")
            self.ent_new_serial.delete(0, 'end');
            self.ent_new_cust.delete(0, 'end');
            self.ent_new_loc.delete(0, 'end')
            self.refresh_table();
            self.tab_search.lift()
        else:
            messagebox.showerror("Error", "Serial exists!")

    def perform_search(self):
        s = self.search_var.get().strip()
        data = get_machine_details(s)
        if data:
            self.update_info_label(data)
        else:
            self.lbl_info.config(text="❌ NOT FOUND", fg="red");
            self.selected_machine = None

    def on_tree_select(self, event):
        selected_item = self.tree.selection()
        if selected_item:
            item = self.tree.item(selected_item)
            vals = item['values']
            # vals = [Serial, Cust, Loc, Pay, Last, Days]
            self.lbl_info.config(text=f"✅ {vals[0]} | {vals[1]} | {vals[2]}", fg="#00E676")
            self.selected_machine = str(vals[0])

    def update_info_label(self, data):
        # data = (id, serial, mfg, pay, cust, loc, last, expiry)
        self.lbl_info.config(text=f"✅ {data[1]} | {data[4]} | {data[5]}", fg="#00E676")
        self.selected_machine = data[1]

    def generate_key(self, days):
        # 1. முதலில் டேபிளில் இருந்து செலக்ட் செய்யப்பட்ட மெஷின் Serial/Hardware ID-ஐ எடுக்கவும்
        selected_item = self.tree.selection()
        if not selected_item:
            messagebox.showwarning("Error", "Select machine from the list!")
            return

        item = self.tree.item(selected_item)
        pc_id = item['values'][0]  # இதுதான் அந்த மெஷினின் Hardware ID (Serial No)

        # 2. இப்போது அந்த Hardware ID-க்கு ஏற்ப கீயை உருவாக்கவும்
        pw = generate_password_logic(pc_id, days)

        # 3. ஹிஸ்டரியை அப்டேட் செய்யவும்
        update_history(pc_id, days)
        self.refresh_table()

        # 4. கஸ்டமருக்கு காட்டும் வகையில் கீயைப் பாப்-அப் செய்யவும்
        self.show_password_popup(pw, days)

    def show_password_popup(self, password, days):
        popup = tk.Toplevel(self.root)
        popup.title("Generated");
        popup.geometry("400x250");
        popup.configure(bg="#1e1e2e");
        popup.grab_set()
        tk.Label(popup, text=f"Valid: {days} Days", font=("Arial", 10), bg="#1e1e2e", fg="gray").pack(pady=(20, 5))
        tk.Label(popup, text=password, font=("Arial", 35, "bold"), bg="#1e1e2e", fg="#50fa7b").pack(pady=10)
        self.root.clipboard_clear();
        self.root.clipboard_append(password);
        self.root.update()
        tk.Label(popup, text="(Copied!)", bg="#1e1e2e", fg="white").pack()
        tk.Button(popup, text="CLOSE", command=popup.destroy).pack(pady=10)

    # --- BOSS FEATURES ---
    def delete_machine_action(self):
        if not hasattr(self, 'selected_machine') or not self.selected_machine: return
        if messagebox.askyesno("CONFIRM DELETE",
                               f"Are you sure you want to delete {self.selected_machine}?\nThis cannot be undone."):
            delete_machine_logic(self.selected_machine)
            self.refresh_table()
            self.lbl_info.config(text="Deleted.", fg="red")
            self.selected_machine = None

    def edit_machine_popup(self):
        if not hasattr(self, 'selected_machine') or not self.selected_machine: return

        # Get current details
        data = get_machine_details(self.selected_machine)
        # data indices: 0=id, 1=serial, 2=mfg, 3=pay, 4=cust, 5=loc

        edit_win = tk.Toplevel(self.root)
        edit_win.title(f"Edit {self.selected_machine}")
        edit_win.geometry("400x300")

        tk.Label(edit_win, text="Customer Name:").pack(pady=5)
        e_cust = tk.Entry(edit_win);
        e_cust.insert(0, data[4]);
        e_cust.pack()

        tk.Label(edit_win, text="Location:").pack(pady=5)
        e_loc = tk.Entry(edit_win);
        e_loc.insert(0, data[5]);
        e_loc.pack()

        tk.Label(edit_win, text="Payment Status:").pack(pady=5)
        e_pay = tk.Entry(edit_win);
        e_pay.insert(0, data[3]);
        e_pay.pack()

        def save_edits():
            update_machine_details(self.selected_machine, e_cust.get(), e_loc.get(), e_pay.get())
            self.refresh_table()
            edit_win.destroy()
            messagebox.showinfo("Updated", "Details updated successfully.")

        tk.Button(edit_win, text="SAVE CHANGES", bg="green", fg="white", command=save_edits).pack(pady=20)

    def refresh_table(self):
        for i in self.tree.get_children(): self.tree.delete(i)
        conn = sqlite3.connect(DB_FILE);
        cursor = conn.cursor()
        cursor.execute(
            "SELECT serial_no, customer_name, location, payment_status, last_password_date, expiry_days FROM machines")
        for row in cursor.fetchall(): self.tree.insert("", "end", values=row)
        conn.close()

    def clear_window(self):
        for widget in self.root.winfo_children(): widget.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = MasterControlApp(root)
    root.mainloop()