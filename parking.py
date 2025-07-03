import sqlite3
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime, timedelta
import threading
import time

# Configuration
CONFIG = {
    "pricing": {
        "hourly_rate": 5.00,
        "currency": "$"
    },
    "expiry_check_interval": 60  # Check for expired bookings every 60 seconds
}

# Database setup
def initialize_database():
    conn = sqlite3.connect('parking.db', check_same_thread=False)
    cursor = conn.cursor()
    
    cursor.execute("DROP TABLE IF EXISTS slots")
    cursor.execute("DROP TABLE IF EXISTS bookings")
    cursor.execute("DROP TABLE IF EXISTS admin_users")
    
    cursor.execute('''
    CREATE TABLE slots (
        slot_id INTEGER PRIMARY KEY,
        status TEXT DEFAULT 'available',
        vehicle_type TEXT DEFAULT 'regular',
        is_active INTEGER DEFAULT 1
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE bookings (
        booking_id INTEGER PRIMARY KEY AUTOINCREMENT,
        slot_id INTEGER,
        user_id TEXT,
        vehicle_number TEXT,
        start_time TEXT,
        end_time TEXT,
        status TEXT DEFAULT 'active',
        amount_paid REAL DEFAULT 0,
        payment_status TEXT DEFAULT 'unpaid',
        FOREIGN KEY (slot_id) REFERENCES slots (slot_id)
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE admin_users (
        username TEXT PRIMARY KEY,
        password TEXT,
        role TEXT
    )
    ''')
    
    for i in range(1, 21):
        cursor.execute("INSERT INTO slots (slot_id) VALUES (?)", (i,))
    
    cursor.execute(
        "INSERT INTO admin_users (username, password, role) VALUES (?, ?, ?)",
        ("admin", "admin123", "superadmin")
    )
    
    conn.commit()
    conn.close()

initialize_database()

class PaymentService:
    @staticmethod
    def calculate_charge(start_time, end_time=None):
        if end_time is None:
            end_time = datetime.now()
        
        if isinstance(start_time, str):
            start_time = datetime.fromisoformat(start_time)
        if isinstance(end_time, str):
            end_time = datetime.fromisoformat(end_time)
        
        duration = (end_time - start_time).total_seconds() / 3600
        return round(duration * CONFIG['pricing']['hourly_rate'], 2)
    
    @staticmethod
    def process_payment(amount):
        print(f"Processing payment of {CONFIG['pricing']['currency']}{amount}")
        return True

class ParkingSystem:
    def __init__(self):
        self.conn = sqlite3.connect('parking.db', check_same_thread=False)
        self.shutdown_flag = False
        # Start background thread for checking expired bookings
        self.expiry_checker = threading.Thread(target=self._expiry_checker_loop)
        self.expiry_checker.daemon = True
        self.expiry_checker.start()
    
    def execute_query(self, query, params=(), fetch=False):
        result = None
        try:
            cursor = self.conn.cursor()
            cursor.execute(query, params)
            if fetch:
                result = cursor.fetchall()
            self.conn.commit()
        except Exception as e:
            print(f"Database error: {e}")
        return result
    
    def get_available_slots(self):
        query = '''
        SELECT slot_id FROM slots 
        WHERE status = 'available' AND is_active = 1
        ORDER BY slot_id
        '''
        result = self.execute_query(query, fetch=True)
        return [row[0] for row in result] if result else []
    
    def book_slot(self, slot_id, user_id, vehicle_number, duration_minutes=60):
        status_check = self.execute_query(
            "SELECT status FROM slots WHERE slot_id = ?", 
            (slot_id,), 
            fetch=True
        )
        
        if not status_check or status_check[0][0] != 'available':
            return False, "Slot is not available"
        
        start_time = datetime.now()
        end_time = start_time + timedelta(minutes=duration_minutes)
        amount = PaymentService.calculate_charge(start_time, end_time)
        
        self.execute_query(
            "UPDATE slots SET status = 'booked' WHERE slot_id = ?", 
            (slot_id,)
        )
        
        self.execute_query('''
        INSERT INTO bookings (
            slot_id, user_id, vehicle_number, 
            start_time, end_time, amount_paid
        ) VALUES (?, ?, ?, ?, ?, ?)
        ''', (slot_id, user_id, vehicle_number, 
              start_time.isoformat(), end_time.isoformat(), amount))
        
        if PaymentService.process_payment(amount):
            self.execute_query(
                "UPDATE bookings SET payment_status = 'paid' WHERE booking_id = ?",
                (self.execute_query("SELECT last_insert_rowid()", fetch=True)[0][0],)
            )
        
        return True, f"Slot {slot_id} booked successfully until {end_time.strftime('%Y-%m-%d %H:%M:%S')}"
    
    def get_user_bookings(self, user_id):
        query = '''
        SELECT 
            b.booking_id, b.slot_id, b.vehicle_number, 
            b.start_time, b.end_time, b.amount_paid, b.payment_status
        FROM bookings b
        WHERE b.user_id = ? AND b.status = 'active'
        ORDER BY b.end_time
        '''
        return self.execute_query(query, (user_id,), fetch=True) or []
    
    def release_slot(self, booking_id):
        cursor = self.conn.cursor()
        
        # Get booking info
        cursor.execute('''
        SELECT slot_id FROM bookings 
        WHERE booking_id = ? AND status = 'active'
        ''', (booking_id,))
        result = cursor.fetchone()
        
        if not result:
            return False, "Booking not found or already released"
        
        slot_id = result[0]
        
        # Update booking status
        cursor.execute('''
        UPDATE bookings SET status = 'completed' 
        WHERE booking_id = ?
        ''', (booking_id,))
        
        # Update slot status
        cursor.execute('''
        UPDATE slots SET status = 'available' 
        WHERE slot_id = ?
        ''', (slot_id,))
        
        self.conn.commit()
        return True, f"Slot {slot_id} released successfully"
    
    def check_expired_bookings(self):
        now = datetime.now().isoformat()
        expired = self.execute_query(
            "SELECT b.booking_id, b.slot_id FROM bookings b WHERE b.status = 'active' AND b.end_time < ?",
            (now,),
            fetch=True
        ) or []
        
        for booking_id, slot_id in expired:
            self.execute_query(
                "UPDATE bookings SET status = 'expired' WHERE booking_id = ?",
                (booking_id,)
            )
            self.execute_query(
                "UPDATE slots SET status = 'available' WHERE slot_id = ?",
                (slot_id,)
            )
            print(f"Auto-released expired booking {booking_id} for slot {slot_id}")
        
        return len(expired)
    
    def _expiry_checker_loop(self):
        """Background thread that periodically checks for expired bookings"""
        while not self.shutdown_flag:
            try:
                expired_count = self.check_expired_bookings()
                if expired_count > 0:
                    print(f"Auto-released {expired_count} expired bookings")
                # Sleep for the configured interval
                time.sleep(CONFIG['expiry_check_interval'])
            except Exception as e:
                print(f"Error in expiry checker: {e}")
                # If an error occurs, wait a bit before trying again
                time.sleep(5)
    
    def get_all_bookings(self, filters=None):
        query = '''
        SELECT 
            b.booking_id, b.slot_id, b.user_id, b.vehicle_number,
            b.start_time, b.end_time, b.status, b.amount_paid, b.payment_status
        FROM bookings b
        '''
        params = []
        
        if filters:
            conditions = []
            if filters.get('status'):
                conditions.append("b.status = ?")
                params.append(filters['status'])
            if filters.get('date'):
                conditions.append("date(b.start_time) = ?")
                params.append(filters['date'])
            
            if conditions:
                query += " WHERE " + " AND ".join(conditions)
        
        query += " ORDER BY b.start_time DESC"
        return self.execute_query(query, params, fetch=True) or []
    
    def close(self):
        # Signal the background thread to stop
        self.shutdown_flag = True
        # Wait for the thread to finish if it's still running
        if hasattr(self, 'expiry_checker') and self.expiry_checker.is_alive():
            self.expiry_checker.join(timeout=2)
        # Close the database connection
        self.conn.close()

class AdminInterface(tk.Toplevel):
    def __init__(self, parent, parking_system):
        super().__init__(parent)
        self.title("Admin Dashboard")
        self.geometry("1200x800")
        self.parking_system = parking_system
        
        # Initialize refresh_timer attribute
        self.refresh_timer = None
        
        self.create_widgets()
        self.load_data()
        
        # Periodically refresh data to show expired bookings
        self.schedule_refresh()
    
    def create_widgets(self):
        main_frame = ttk.Frame(self, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        filter_frame = ttk.Frame(main_frame)
        filter_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(filter_frame, text="Status:").pack(side=tk.LEFT, padx=5)
        self.status_var = tk.StringVar(value='all')
        ttk.OptionMenu(filter_frame, self.status_var, 'all', 'all', 'active', 'completed', 'expired').pack(side=tk.LEFT, padx=5)
        
        ttk.Label(filter_frame, text="Date:").pack(side=tk.LEFT, padx=5)
        self.date_entry = ttk.Entry(filter_frame)
        self.date_entry.pack(side=tk.LEFT, padx=5)
        
        ttk.Button(filter_frame, text="Apply Filters", command=self.load_data).pack(side=tk.LEFT, padx=5)
        
        self.tree = ttk.Treeview(main_frame, columns=(
            "booking_id", "slot_id", "user_id", "vehicle", 
            "start_time", "end_time", "status", "amount", "payment"
        ), show="headings")
        
        columns = [
            ("booking_id", "Booking ID", 80),
            ("slot_id", "Slot", 60),
            ("user_id", "User ID", 100),
            ("vehicle", "Vehicle", 120),
            ("start_time", "Start Time", 150),
            ("end_time", "End Time", 150),
            ("status", "Status", 80),
            ("amount", "Amount", 80),
            ("payment", "Payment", 80)
        ]
        
        for col, heading, width in columns:
            self.tree.heading(col, text=heading)
            self.tree.column(col, width=width, anchor="center")
        
        self.tree.pack(fill=tk.BOTH, expand=True)
        
        stats_frame = ttk.Frame(main_frame)
        stats_frame.pack(fill=tk.X, pady=10)
        
        self.total_label = ttk.Label(stats_frame, text="Total Bookings: 0")
        self.total_label.pack(side=tk.LEFT, padx=10)
        
        self.active_label = ttk.Label(stats_frame, text="Active: 0")
        self.active_label.pack(side=tk.LEFT, padx=10)
        
        self.revenue_label = ttk.Label(stats_frame, text="Today's Revenue: $0.00")
        self.revenue_label.pack(side=tk.LEFT, padx=10)
        
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=5)
        
        ttk.Button(btn_frame, text="Refresh", command=self.load_data).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Manage Slots", command=self.open_slot_management).pack(side=tk.LEFT, padx=5)
    
    def load_data(self):
        filters = {}
        if self.status_var.get() != 'all':
            filters['status'] = self.status_var.get()
        if self.date_entry.get():
            filters['date'] = self.date_entry.get()
        
        bookings = self.parking_system.get_all_bookings(filters)
        
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        for booking in bookings:
            start_time = datetime.fromisoformat(booking[4]).strftime('%Y-%m-%d %H:%M')
            end_time = datetime.fromisoformat(booking[5]).strftime('%Y-%m-%d %H:%M')
            
            self.tree.insert("", "end", values=(
                booking[0], booking[1], booking[2], 
                booking[3], start_time, end_time,
                booking[6], f"{CONFIG['pricing']['currency']}{booking[7]}", booking[8]
            ))
        
        self.update_stats()
        # Reschedule the next refresh
        self.schedule_refresh()
    
    def schedule_refresh(self):
        # Cancel any existing timer
        if self.refresh_timer:
            self.after_cancel(self.refresh_timer)
        # Schedule next refresh in 30 seconds
        self.refresh_timer = self.after(30000, self.load_data)
    
    def update_stats(self):
        total = self.parking_system.execute_query(
            "SELECT COUNT(*) FROM bookings", fetch=True
        )[0][0]
        self.total_label.config(text=f"Total Bookings: {total}")
        
        active = self.parking_system.execute_query(
            "SELECT COUNT(*) FROM bookings WHERE status = 'active'", fetch=True
        )[0][0]
        self.active_label.config(text=f"Active: {active}")
        
        today = datetime.now().strftime('%Y-%m-%d')
        revenue = self.parking_system.execute_query(
            "SELECT SUM(amount_paid) FROM bookings WHERE payment_status = 'paid' AND date(start_time) = ?",
            (today,), fetch=True
        )[0][0] or 0
        self.revenue_label.config(text=f"Today's Revenue: {CONFIG['pricing']['currency']}{revenue:.2f}")
    
    def open_slot_management(self):
        SlotManagementWindow(self, self.parking_system)
    
    def destroy(self):
        # Cancel any pending timer before destroying
        if self.refresh_timer:
            self.after_cancel(self.refresh_timer)
        super().destroy()

class SlotManagementWindow(tk.Toplevel):
    def __init__(self, parent, parking_system):
        super().__init__(parent)
        self.title("Slot Management")
        self.geometry("600x400")
        self.parking_system = parking_system
        
        self.create_widgets()
        self.load_slots()
    
    def create_widgets(self):
        main_frame = ttk.Frame(self, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        self.tree = ttk.Treeview(main_frame, columns=("slot_id", "status", "vehicle_type", "actions"), show="headings")
        self.tree.heading("slot_id", text="Slot ID")
        self.tree.heading("status", text="Status")
        self.tree.heading("vehicle_type", text="Vehicle Type")
        self.tree.heading("actions", text="Actions")
        
        self.tree.column("slot_id", width=80, anchor="center")
        self.tree.column("status", width=100, anchor="center")
        self.tree.column("vehicle_type", width=100, anchor="center")
        self.tree.column("actions", width=150, anchor="center")
        
        self.tree.pack(fill=tk.BOTH, expand=True)
        
        ttk.Button(main_frame, text="Add New Slot", command=self.add_slot).pack(pady=5)
    
    def load_slots(self):
        slots = self.parking_system.execute_query(
            "SELECT slot_id, status, vehicle_type FROM slots ORDER BY slot_id", fetch=True
        ) or []
        
        for item in self.tree.get_children():
            self.tree.delete(item)
        
        for slot in slots:
            self.tree.insert("", "end", values=(
                slot[0], slot[1], slot[2], "Toggle Status"
            ))
        
        self.tree.bind("<Button-1>", self.on_slot_click)
    
    def on_slot_click(self, event):
        item = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        
        if item and column == "#4":
            slot_id = self.tree.item(item, "values")[0]
            self.toggle_slot_status(slot_id)
    
    def toggle_slot_status(self, slot_id):
        current_status = self.parking_system.execute_query(
            "SELECT is_active FROM slots WHERE slot_id = ?", (slot_id,), fetch=True
        )[0][0]
        
        new_status = 0 if current_status else 1
        self.parking_system.execute_query(
            "UPDATE slots SET is_active = ? WHERE slot_id = ?", (new_status, slot_id)
        )
        
        self.load_slots()
        messagebox.showinfo("Success", f"Slot {slot_id} status updated")
    
    def add_slot(self):
        max_id = self.parking_system.execute_query(
            "SELECT MAX(slot_id) FROM slots", fetch=True
        )[0][0] or 0
        
        new_slot_id = max_id + 1
        self.parking_system.execute_query(
            "INSERT INTO slots (slot_id) VALUES (?)", (new_slot_id,)
        )
        
        self.load_slots()
        messagebox.showinfo("Success", f"Added new slot {new_slot_id}")

class ParkingApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Parking Slot Booking System")
        self.root.geometry("1000x1000")
        self.parking_system = ParkingSystem()
        
        # Initialize refresh_timer attribute
        self.refresh_timer = None
        
        self.create_widgets()
        self.update_slot_display()
        
        # Set up periodic refresh timer
        self.schedule_refresh()
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
    
    def create_widgets(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        ttk.Button(
            main_frame, 
            text="Admin Login", 
            command=self.show_admin_login
        ).pack(anchor="ne", padx=10, pady=5)
        
        slots_frame = ttk.LabelFrame(main_frame, text="Available Parking Slots", padding="10")
        slots_frame.pack(fill=tk.X, pady=5)
        
        self.slots_canvas = tk.Canvas(slots_frame)
        self.slots_canvas.pack(fill=tk.X, expand=True)
        
        scrollbar = ttk.Scrollbar(slots_frame, orient="horizontal", command=self.slots_canvas.xview)
        scrollbar.pack(fill=tk.X)
        
        self.slots_canvas.configure(xscrollcommand=scrollbar.set)
        self.slots_frame = ttk.Frame(self.slots_canvas)
        self.slots_canvas.create_window((0, 0), window=self.slots_frame, anchor="nw")
        
        booking_frame = ttk.LabelFrame(main_frame, text="Book a Slot", padding="10")
        booking_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(booking_frame, text="User ID:").grid(row=0, column=0, padx=5, pady=5, sticky="e")
        self.user_id_entry = ttk.Entry(booking_frame)
        self.user_id_entry.grid(row=0, column=1, padx=5, pady=5, sticky="we")
        
        ttk.Label(booking_frame, text="Vehicle Number:").grid(row=1, column=0, padx=5, pady=5, sticky="e")
        self.vehicle_entry = ttk.Entry(booking_frame)
        self.vehicle_entry.grid(row=1, column=1, padx=5, pady=5, sticky="we")
        
        ttk.Label(booking_frame, text="Duration (minutes):").grid(row=2, column=0, padx=5, pady=5, sticky="e")
        self.duration_entry = ttk.Entry(booking_frame)
        self.duration_entry.insert(0, "60")
        self.duration_entry.grid(row=2, column=1, padx=5, pady=5, sticky="we")
        
        self.book_button = ttk.Button(booking_frame, text="Book Selected Slot", command=self.book_slot)
        self.book_button.grid(row=3, column=0, columnspan=2, pady=10)
        
        bookings_frame = ttk.LabelFrame(main_frame, text="Your Active Bookings", padding="10")
        bookings_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        columns = [
            ("booking_id", "Booking ID"),
            ("slot_id", "Slot"),
            ("vehicle", "Vehicle"),
            ("start_time", "Start Time"),
            ("end_time", "End Time"), 
            ("amount", "Amount"),
            ("payment", "Payment"),
            ("actions", "Actions")
        ]
        
        self.bookings_tree = ttk.Treeview(bookings_frame, columns=[col[0] for col in columns], show="headings")
        
        for col, heading in columns:
            self.bookings_tree.heading(col, text=heading)
            width = 100 if col not in ["actions", "vehicle"] else 120
            anchor = "center" if col != "actions" else "center"
            self.bookings_tree.column(col, width=width, anchor=anchor)
        
        self.bookings_tree.pack(fill=tk.BOTH, expand=True)
        
        ttk.Button(
            bookings_frame, 
            text="Refresh Bookings", 
            command=self.update_bookings_display
        ).pack(pady=5)
        
        # Add a status label to show automatic slot release information
        self.status_label = ttk.Label(main_frame, text="Auto-releasing expired slots every 60 seconds")
        self.status_label.pack(pady=5)
        
        booking_frame.columnconfigure(1, weight=1)
    
    def schedule_refresh(self):
        # Cancel any existing timer
        if hasattr(self, 'refresh_timer') and self.refresh_timer:
            self.root.after_cancel(self.refresh_timer)
        # Schedule next refresh in 30 seconds
        self.refresh_timer = self.root.after(30000, self.update_slot_display)
    
    def update_slot_display(self):
        # Make sure we have the slots frame
        if not hasattr(self, 'slots_frame'):
            return
            
        for widget in self.slots_frame.winfo_children():
            widget.destroy()
        
        expired_count = self.parking_system.check_expired_bookings()
        if expired_count:
            self.status_label.config(text=f"Auto-released {expired_count} expired bookings (Last check: {datetime.now().strftime('%H:%M:%S')})")
        else:
            self.status_label.config(text=f"No expired bookings found (Last check: {datetime.now().strftime('%H:%M:%S')})")
        
        available_slots = self.parking_system.get_available_slots()
        
        for i, slot_id in enumerate(available_slots):
            btn = ttk.Button(
                self.slots_frame,
                text=f"Slot {slot_id}",
                command=lambda sid=slot_id: self.select_slot(sid)
            )
            btn.grid(row=i//5, column=i%5, padx=5, pady=5)
        
        self.slots_frame.update_idletasks()
        self.slots_canvas.config(scrollregion=self.slots_canvas.bbox("all"))
        
        self.update_bookings_display()
        # Schedule the next refresh
        self.schedule_refresh()
    
    def select_slot(self, slot_id):
        self.selected_slot = slot_id
        messagebox.showinfo("Slot Selected", f"Slot {slot_id} selected for booking")
    
    def book_slot(self):
        if not hasattr(self, 'selected_slot'):
            messagebox.showerror("Error", "Please select a slot first")
            return
        
        user_id = self.user_id_entry.get().strip()
        vehicle_number = self.vehicle_entry.get().strip()
        duration = self.duration_entry.get().strip()
        
        if not all([user_id, vehicle_number]):
            messagebox.showerror("Error", "Please fill all required fields")
            return
        
        try:
            duration = int(duration)
            if duration <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Error", "Please enter a valid positive number for duration")
            return
        
        success, message = self.parking_system.book_slot(
            self.selected_slot, 
            user_id,
            vehicle_number, 
            duration
        )
        
        if success:
            messagebox.showinfo("Success", message)
            # First update bookings, then schedule refresh
            self.update_bookings_display()
            self.update_slot_display()
            delattr(self, 'selected_slot')
        else:
            messagebox.showerror("Error", message)
    
    def update_bookings_display(self):
        for item in self.bookings_tree.get_children():
            self.bookings_tree.delete(item)
        
        user_id = self.user_id_entry.get().strip()
        if not user_id:
            return
        
        bookings = self.parking_system.get_user_bookings(user_id)
        
        for booking in bookings:
            booking_id, slot_id, vehicle, start_time, end_time, amount, payment_status = booking
            start_time = datetime.fromisoformat(start_time).strftime('%Y-%m-%d %H:%M')
            end_time = datetime.fromisoformat(end_time).strftime('%Y-%m-%d %H:%M')
            
            self.bookings_tree.insert("", "end", values=(
                booking_id,
                slot_id,
                vehicle,
                start_time,
                end_time,
                f"{CONFIG['pricing']['currency']}{amount}",
                payment_status.capitalize(),
                "Release"
            ))
        
        self.bookings_tree.bind("<Button-1>", self.on_booking_click)
    
    def on_booking_click(self, event):
        item = self.bookings_tree.identify_row(event.y)
        column = self.bookings_tree.identify_column(event.x)
        
        if item and column == "#8":
            booking_id = self.bookings_tree.item(item, "values")[0]
            self.release_booking(booking_id)
    
    def release_booking(self, booking_id):
        if messagebox.askyesno("Confirm", "Are you sure you want to release this booking?"):
            success, message = self.parking_system.release_slot(booking_id)
            if success:
                messagebox.showinfo("Success", message)
                self.update_slot_display()
            else:
                messagebox.showerror("Error", message)
    
    def show_admin_login(self):
        login_window = tk.Toplevel(self.root)
        login_window.title("Admin Login")
        login_window.geometry("300x200")
        
        ttk.Label(login_window, text="Username:").pack(pady=5)
        self.admin_user_entry = ttk.Entry(login_window)
        self.admin_user_entry.pack(pady=5)
        
        ttk.Label(login_window, text="Password:").pack(pady=5)
        self.admin_pass_entry = ttk.Entry(login_window, show="*")
        self.admin_pass_entry.pack(pady=5)
        
        ttk.Button(
            login_window, 
            text="Login", 
            command=self.authenticate_admin
        ).pack(pady=10)
    
    def authenticate_admin(self):
        username = self.admin_user_entry.get()
        password = self.admin_pass_entry.get()
        
        admin = self.parking_system.execute_query(
            "SELECT * FROM admin_users WHERE username = ? AND password = ?",
            (username, password), fetch=True
        )
        
        if admin:
            self.admin_user_entry.master.destroy()
            AdminInterface(self.root, self.parking_system)
        else:
            messagebox.showerror("Error", "Invalid admin credentials")
    
    def on_close(self):
        # Cancel any pending timer
        if hasattr(self, 'refresh_timer') and self.refresh_timer:
            self.root.after_cancel(self.refresh_timer)
        self.parking_system.close()
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = ParkingApp(root)
    root.mainloop()
