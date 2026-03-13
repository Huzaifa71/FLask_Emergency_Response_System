from flask import Flask, render_template, request, redirect, url_for, jsonify, session, send_file, flash
import sqlite3
import hashlib
import json
import re
import pytz
from datetime import datetime, time
from contextlib import contextmanager
from functools import wraps
import pandas as pd
from io import BytesIO
import os

app = Flask(__name__)

# --- CONFIGURATION ---
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'ap56cjf92h5c63jc6r3mz7zp21we0vqp')

# Pass
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'PAKIT123')
USER_PASSWORD = os.environ.get('USER_PASSWORD', 'ykk123')

ADMIN_PASS_HASH = hashlib.sha256(ADMIN_PASSWORD.encode()).hexdigest()
USER_PASS_HASH = hashlib.sha256(USER_PASSWORD.encode()).hexdigest()

DEFAULT_COL_PHONE = "Phone"
DB_NAME = "responses.db"

# Default settings
DEFAULT_TIMEZONE = "Asia/Karachi"
DEFAULT_POLL_START = datetime(2026, 1, 1, 9, 0)
DEFAULT_POLL_END = datetime(2026, 1, 31, 18, 0)
DEFAULT_PHONE_VALIDATION_MODE = "flexible"
DEFAULT_TIME_FORMAT = "12"

# File upload configuration
ALLOWED_EXTENSIONS = {'xlsx'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

# --- HELPER FUNCTIONS ---

def hash_password(password):
    """Hash password using SHA256"""
    return hashlib.sha256(password.encode()).hexdigest()

def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def validate_phone(phone, mode="flexible"):
    """Validate phone based on validation mode"""
    patterns = {
        "strict": r'^(\+92|92|0)?3[0-9]{9}$',
        "flexible": r'^\+?[0-9]{7,15}$'
    }
    pattern = patterns.get(mode, patterns["flexible"])
    return re.match(pattern, phone) is not None

def clean_phone(phone):
    """Clean phone number by removing special characters"""
    return str(phone).strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")

def normalize_for_comparison(phone):
    """Normalize phone number for comparison"""
    phone = clean_phone(phone)
    if phone.startswith("+92"):
        return phone[3:]
    elif phone.startswith("92") and len(phone) > 10:
        return phone[2:]
    elif phone.startswith("0"):
        return phone[1:]
    return phone

def format_time_display(dt, time_format="24"):
    """Format datetime for display"""
    if time_format == "12":
        return dt.strftime('%B %d, %Y at %I:%M %p')
    else:
        return dt.strftime('%B %d, %Y at %H:%M')

def get_display_name(user_info):
    """Extract name and department from user info"""
    name = user_info.get('Name', user_info.get('name', user_info.get('Employee Name', '')))
    dept = user_info.get('Department', user_info.get('department', user_info.get('Dept', '')))
    
    parts = []
    if name and str(name).lower() != 'nan':
        parts.append(str(name))
    if dept and str(dept).lower() != 'nan':
        parts.append(str(dept))
    
    if not parts:
        parts = [str(v) for v in user_info.values() if v and str(v).lower() != 'nan'][:2]
    
    return " | ".join(parts) if parts else "User"

# --- DATABASE FUNCTIONS ---

@contextmanager
def get_db():
    """Context manager for database connections - cPanel optimized"""
    # Increased timeout to 30 seconds for concurrent access
    conn = sqlite3.connect(DB_NAME, check_same_thread=False, timeout=30)
    
    # Enable WAL mode for better concurrent access
    conn.execute('PRAGMA journal_mode=WAL;')
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    """Initialize database schema"""
    with get_db() as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS poll_results 
                     (phone TEXT, response TEXT, timestamp TEXT,
                      PRIMARY KEY (phone))''')
        c.execute('''CREATE TABLE IF NOT EXISTS employees 
                     (phone TEXT PRIMARY KEY, info TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS settings
                     (key TEXT PRIMARY KEY, value TEXT)''')
        c.execute('''CREATE INDEX IF NOT EXISTS idx_timestamp 
                     ON poll_results(timestamp)''')
        conn.commit()

def get_setting(key, default=None):
    """Get a setting from database"""
    try:
        with get_db() as conn:
            result = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return result[0] if result else default
    except Exception:
        return default

def set_setting(key, value):
    """Set a setting in database"""
    try:
        with get_db() as conn:
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
            conn.commit()
    except Exception as e:
        print(f"Error setting {key}: {e}")

def get_poll_config():
    """Get current poll configuration"""
    start_str = get_setting("poll_start")
    end_str = get_setting("poll_end")
    validation_mode = get_setting("validation_mode", DEFAULT_PHONE_VALIDATION_MODE)
    timezone_str = get_setting("timezone", DEFAULT_TIMEZONE)
    time_format = get_setting("time_format", DEFAULT_TIME_FORMAT)
    col_phone = get_setting("col_phone", DEFAULT_COL_PHONE)
    
    try:
        tz = pytz.timezone(timezone_str)
    except:
        tz = pytz.timezone(DEFAULT_TIMEZONE)
    
    if start_str:
        try:
            poll_start = tz.localize(datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S"))
        except:
            poll_start = tz.localize(DEFAULT_POLL_START)
    else:
        poll_start = tz.localize(DEFAULT_POLL_START)
    
    if end_str:
        try:
            poll_end = tz.localize(datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S"))
        except:
            poll_end = tz.localize(DEFAULT_POLL_END)
    else:
        poll_end = tz.localize(DEFAULT_POLL_END)
    
    return poll_start, poll_end, validation_mode, tz, time_format, col_phone

def has_already_voted(phone):
    """Check if user has already voted"""
    try:
        with get_db() as conn:
            result = conn.execute("SELECT phone FROM poll_results WHERE phone=?", (phone,)).fetchone()
        return result is not None
    except:
        return False

def get_employee(phone):
    """Get employee information by phone"""
    try:
        with get_db() as conn:
            result = conn.execute("SELECT info FROM employees WHERE phone=?", (phone,)).fetchone()
            
            if result:
                return json.loads(result[0])
            
            normalized_input = normalize_for_comparison(phone)
            all_employees = conn.execute("SELECT phone, info FROM employees").fetchall()
            for emp_phone, emp_info in all_employees:
                if normalize_for_comparison(emp_phone) == normalized_input:
                    return json.loads(emp_info)
    except:
        pass
    
    return None

def save_vote(phone, response, timestamp):
    """Save a poll response"""
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO poll_results (phone, response, timestamp) VALUES (?, ?, ?)",
                (phone, response, timestamp)
            )
            conn.commit()
    except Exception as e:
        print(f"Error saving vote: {e}")
        raise

def get_total_employees():
    """Get total number of employees"""
    try:
        with get_db() as conn:
            result = conn.execute("SELECT COUNT(*) FROM employees").fetchone()
        return result[0] if result else 0
    except:
        return 0

def get_admin_stats_data():
    """Get statistics for admin dashboard"""
    try:
        with get_db() as conn:
            df_results = pd.read_sql_query("SELECT * FROM poll_results", conn)
            
        total_employees = get_total_employees()
        total_voted = len(df_results)
        total_not_voted = total_employees - total_voted
        participation_rate = round((total_voted / total_employees * 100), 1) if total_employees > 0 else 0
        
        ok_count = len(df_results[df_results['response'] == 'I am okay and safe.']) if not df_results.empty else 0
        not_ok_count = len(df_results[df_results['response'] == 'I am stuck but help not needed.']) if not df_results.empty else 0
        help_needed_count = len(df_results[df_results['response'] == 'I am stuck and help is needed.']) if not df_results.empty else 0
        
        # Get table data
        table_data = []
        if not df_results.empty:
            with get_db() as conn:
                df_emps = pd.read_sql_query("SELECT * FROM employees", conn)
            
            if not df_emps.empty:
                df_emps_expanded = pd.concat([
                    df_emps.drop(['info'], axis=1),
                    df_emps['info'].apply(lambda x: pd.Series(json.loads(x)))
                ], axis=1)
                
                final_df = pd.merge(df_results, df_emps_expanded, on="phone", how="left")
                table_data = final_df.to_dict('records')
        
        return {
            'total_employees': total_employees,
            'total_voted': total_voted,
            'total_not_voted': total_not_voted,
            'participation_rate': participation_rate,
            'ok_count': ok_count,
            'not_ok_count': not_ok_count,
            'help_needed_count': help_needed_count,
            'table_data': table_data
        }
    except Exception as e:
        print(f"Error getting stats: {e}")
        return {
            'total_employees': 0,
            'total_voted': 0,
            'total_not_voted': 0,
            'participation_rate': 0,
            'ok_count': 0,
            'not_ok_count': 0,
            'help_needed_count': 0,
            'table_data': []
        }

# --- DECORATORS ---

def login_required(f):
    """Decorator for user login required"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_phone' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """Decorator for admin login required"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('admin_logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- ROUTES ---

@app.route('/')
def index():
    """Redirect to appropriate page"""
    if session.get('admin_logged_in'):
        return redirect(url_for('admin_results'))
    elif session.get('user_phone'):
        return redirect(url_for('user_interface'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page for both staff and admin"""
    if request.method == 'POST':
        login_type = request.form.get('login_type')
        
        if login_type == 'staff':
            phone_input = request.form.get('phone')
            password = request.form.get('password')
            
            if not phone_input or not password:
                flash('Please fill in all fields.', 'error')
                return render_template('login.html')
            
            clean = clean_phone(phone_input)
            _, _, PHONE_VALIDATION_MODE, _, _, _ = get_poll_config()
            
            if not validate_phone(clean, PHONE_VALIDATION_MODE):
                flash('Invalid phone number format.', 'error')
                return render_template('login.html')
            
            if hash_password(password) != USER_PASS_HASH:
                flash('Invalid password.', 'error')
                return render_template('login.html')
            
            user_info = get_employee(clean)
            if user_info:
                session['user_phone'] = clean
                session['user_info'] = user_info
                return redirect(url_for('user_interface'))
            else:
                flash('Phone number not found in employee database.', 'error')
                return render_template('login.html')
        
        elif login_type == 'admin':
            admin_password = request.form.get('admin_password')
            
            if hash_password(admin_password) == ADMIN_PASS_HASH:
                session['admin_logged_in'] = True
                return redirect(url_for('admin_dashboard'))
            else:
                flash('Invalid admin password.', 'error')
                return render_template('login.html')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    """Logout for both users and admin"""
    session.clear()
    flash('Logged out successfully.', 'success')
    return redirect(url_for('login'))

@app.route('/user')
@login_required
def user_interface():
    """User interface for staff members"""
    POLL_START, POLL_END, _, TZ, TIME_FORMAT, _ = get_poll_config()
    
    user_info = session.get('user_info', {})
    user_phone = session.get('user_phone')
    display_name = get_display_name(user_info)
    
    now = datetime.now(TZ)
    remaining = POLL_END - now
    
    # Calculate remaining time
    days = remaining.days
    hours, remainder = divmod(remaining.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    timer_str = f"{days}d {hours}h {minutes}m" if days > 0 else f"{hours}h {minutes}m"
    
    # Check poll status
    poll_not_started = now < POLL_START
    poll_ended = now > POLL_END
    already_voted = has_already_voted(user_phone)
    poll_active = POLL_START <= now <= POLL_END and not already_voted
    
    return render_template('user_interface.html',
                         display_name=display_name,
                         timer_str=timer_str,
                         poll_not_started=poll_not_started,
                         poll_ended=poll_ended,
                         already_voted=already_voted,
                         poll_active=poll_active,
                         poll_start=format_time_display(POLL_START, TIME_FORMAT),
                         poll_end=format_time_display(POLL_END, TIME_FORMAT))

@app.route('/submit_vote', methods=['POST'])
@login_required
def submit_vote():
    """Submit user vote - uses Post/Redirect/Get pattern"""
    response = request.form.get('response')
    user_phone = session.get('user_phone')
    
    if not response:
        flash('Please select a response.', 'error')
        return redirect(url_for('user_interface'))
    
    _, _, _, TZ, _, _ = get_poll_config()
    now = datetime.now(TZ)
    
    if has_already_voted(user_phone):
        flash('You have already submitted your response.', 'warning')
        return redirect(url_for('user_interface'))
    
    try:
        timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S")
        save_vote(user_phone, response, timestamp_str)
        flash('Response submitted successfully!', 'success')
    except Exception as e:
        flash('Error submitting response. Please try again.', 'error')
    
    return redirect(url_for('user_interface'))

@app.route('/admin')
@admin_required
def admin_dashboard():
    """Admin dashboard - redirect to results"""
    return redirect(url_for('admin_results'))

@app.route('/admin/results')
@admin_required
def admin_results():
    """Admin results view"""
    stats = get_admin_stats_data()
    
    return render_template('admin_results.html',
                         active_tab='results',
                         total_employees=stats['total_employees'],
                         total_voted=stats['total_voted'],
                         total_not_voted=stats['total_not_voted'],
                         participation_rate=f"{stats['participation_rate']}",
                         ok_count=stats['ok_count'],
                         not_ok_count=stats['not_ok_count'],
                         help_needed_count=stats['help_needed_count'],
                         results_data=stats['table_data'],
                         has_data=len(stats['table_data']) > 0)

@app.route('/api/stats')
@admin_required
def api_stats():
    """API endpoint for live stats updates"""
    stats = get_admin_stats_data()
    return jsonify(stats)

@app.route('/admin/users', methods=['GET', 'POST'])
@admin_required
def admin_users():
    """Admin users management"""
    _, _, PHONE_VALIDATION_MODE, _, _, COL_PHONE = get_poll_config()
    
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file uploaded.', 'error')
            return redirect(url_for('admin_users'))
        
        file = request.files['file']
        
        if file.filename == '':
            flash('No file selected.', 'error')
            return redirect(url_for('admin_users'))
        
        if not allowed_file(file.filename):
            flash('Please upload an Excel file (.xlsx).', 'error')
            return redirect(url_for('admin_users'))
        
        try:
            df_upload = pd.read_excel(file, dtype=str)
            
            if COL_PHONE not in df_upload.columns:
                flash(f"Missing '{COL_PHONE}' column. Available: {', '.join(df_upload.columns)}", 'error')
                return redirect(url_for('admin_users'))
            
            df_upload[COL_PHONE] = df_upload[COL_PHONE].apply(clean_phone)
            
            valid_df = df_upload[df_upload[COL_PHONE].apply(lambda x: validate_phone(x, PHONE_VALIDATION_MODE))]
            invalid_count = len(df_upload) - len(valid_df)
            
            # Upload to database
            with get_db() as conn:
                conn.execute("DELETE FROM employees")
                for _, row in valid_df.iterrows():
                    phone = row[COL_PHONE]
                    info_dict = row.drop(COL_PHONE).to_dict()
                    
                    for key, value in info_dict.items():
                        if pd.isna(value):
                            info_dict[key] = None
                        elif isinstance(value, (pd.Timestamp, datetime)):
                            info_dict[key] = value.strftime('%Y-%m-%d')
                    
                    info_json = json.dumps(info_dict)
                    conn.execute(
                        "INSERT OR IGNORE INTO employees (phone, info) VALUES (?, ?)",
                        (phone, info_json)
                    )
                conn.commit()
            
            msg = f"Uploaded {len(valid_df)} employees successfully."
            if invalid_count > 0:
                msg += f" ({invalid_count} invalid phone numbers skipped)"
            flash(msg, 'success')
            
            return redirect(url_for('admin_users'))
            
        except Exception as e:
            flash(f"Error uploading file: {str(e)}", 'error')
            return redirect(url_for('admin_users'))
    
    # GET request - show current employees
    try:
        with get_db() as conn:
            current_emps = pd.read_sql_query("SELECT * FROM employees", conn)
        
        employees_data = []
        if not current_emps.empty:
            df_preview = pd.concat([
                current_emps.drop(['info'], axis=1),
                current_emps['info'].apply(lambda x: pd.Series(json.loads(x)))
            ], axis=1)
            employees_data = df_preview.to_dict('records')
    except:
        employees_data = []
    
    return render_template('admin_users.html',
                         active_tab='users',
                         employees_data=employees_data,
                         employee_count=len(employees_data),
                         col_phone=COL_PHONE)

@app.route('/admin/settings', methods=['GET', 'POST'])
@admin_required
def admin_settings():
    """Admin settings page"""
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'save_settings':
            try:
                # Get form data
                timezone_str = request.form.get('timezone')
                time_format = request.form.get('time_format')
                validation_mode = request.form.get('validation_mode')
                col_phone = request.form.get('col_phone', '').strip()
                
                start_date = request.form.get('start_date')
                start_time = request.form.get('start_time')
                end_date = request.form.get('end_date')
                end_time = request.form.get('end_time')
                
                # Validate inputs
                if not all([timezone_str, time_format, validation_mode, col_phone, 
                           start_date, start_time, end_date, end_time]):
                    flash('All fields are required.', 'error')
                    return redirect(url_for('admin_settings'))
                
                # Parse and validate
                tz = pytz.timezone(timezone_str)
                start_dt = tz.localize(datetime.strptime(f"{start_date} {start_time}", "%Y-%m-%d %H:%M"))
                end_dt = tz.localize(datetime.strptime(f"{end_date} {end_time}", "%Y-%m-%d %H:%M"))
                
                if end_dt <= start_dt:
                    flash('End date/time must be after start date/time.', 'error')
                    return redirect(url_for('admin_settings'))
                
                # Save to database
                set_setting("poll_start", start_dt.strftime("%Y-%m-%d %H:%M:%S"))
                set_setting("poll_end", end_dt.strftime("%Y-%m-%d %H:%M:%S"))
                set_setting("validation_mode", validation_mode)
                set_setting("timezone", timezone_str)
                set_setting("time_format", time_format)
                set_setting("col_phone", col_phone)
                
                flash('Settings saved successfully!', 'success')
                return redirect(url_for('admin_settings'))
                
            except Exception as e:
                flash(f"Error saving settings: {str(e)}", 'error')
                return redirect(url_for('admin_settings'))
        
        elif action == 'clear_responses':
            try:
                with get_db() as conn:
                    conn.execute("DELETE FROM poll_results")
                    conn.commit()
                flash('All responses cleared!', 'success')
            except Exception as e:
                flash(f"Error: {str(e)}", 'error')
            return redirect(url_for('admin_settings'))
        
        elif action == 'reset_database':
            try:
                with get_db() as conn:
                    conn.execute("DELETE FROM poll_results")
                    conn.execute("DELETE FROM employees")
                    conn.commit()
                flash('Database completely reset!', 'success')
            except Exception as e:
                flash(f"Error: {str(e)}", 'error')
            return redirect(url_for('admin_settings'))
        
        elif action == 'reset_settings':
            try:
                with get_db() as conn:
                    conn.execute("DELETE FROM settings")
                    conn.commit()
                flash('Settings reset to defaults!', 'success')
            except Exception as e:
                flash(f"Error: {str(e)}", 'error')
            return redirect(url_for('admin_settings'))
    
    # GET request - show settings form
    POLL_START, POLL_END, PHONE_VALIDATION_MODE, TZ, TIME_FORMAT, COL_PHONE = get_poll_config()
    
    common_timezones = ["Asia/Karachi", "Asia/Dubai", "Asia/Kolkata", "Asia/Shanghai", 
                       "Europe/London", "Europe/Paris", "US/Eastern", "US/Pacific", "UTC"]
    
    return render_template('admin_settings.html',
                         active_tab='settings',
                         timezones=common_timezones,
                         current_timezone=str(TZ),
                         current_time_format=TIME_FORMAT,
                         current_validation=PHONE_VALIDATION_MODE,
                         current_col_phone=COL_PHONE,
                         poll_start=POLL_START,
                         poll_end=POLL_END)

@app.route('/admin/download/<report_type>')
@admin_required
def download_report(report_type):
    """Download Excel reports"""
    try:
        with get_db() as conn:
            df_results = pd.read_sql_query("SELECT * FROM poll_results", conn)
            df_emps = pd.read_sql_query("SELECT * FROM employees", conn)
        
        output = BytesIO()
        
        if report_type == 'voted':
            if df_results.empty:
                flash('No voted employees to download.', 'warning')
                return redirect(url_for('admin_results'))
            
            df_emps_expanded = pd.concat([
                df_emps.drop(['info'], axis=1),
                df_emps['info'].apply(lambda x: pd.Series(json.loads(x)))
            ], axis=1)
            
            final_df = pd.merge(df_results, df_emps_expanded, on="phone", how="left")
            
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                final_df.to_excel(writer, sheet_name='Voted Employees', index=False)
                
                # Add summary sheet
                stats = get_admin_stats_data()
                summary_data = {
                    'Metric': ['Total Employees', 'Total Voted', 'Participation Rate', 
                              'OK Responses', 'Stuck (No Help)', 'Help Needed'],
                    'Value': [
                        stats['total_employees'], 
                        stats['total_voted'], 
                        f"{stats['participation_rate']}%",
                        stats['ok_count'], 
                        stats['not_ok_count'], 
                        stats['help_needed_count']
                    ]
                }
                summary_df = pd.DataFrame(summary_data)
                summary_df.to_excel(writer, sheet_name='Summary', index=False)
            
            output.seek(0)
            filename = f"voted_employees_{datetime.now().strftime('%Y%m%d')}.xlsx"
            
        elif report_type == 'not_voted':
            voted_phones = set(df_results['phone'].tolist()) if not df_results.empty else set()
            
            df_emps_expanded = pd.concat([
                df_emps.drop(['info'], axis=1),
                df_emps['info'].apply(lambda x: pd.Series(json.loads(x)))
            ], axis=1)
            
            not_voted_df = df_emps_expanded[~df_emps_expanded['phone'].isin(voted_phones)]
            
            if not_voted_df.empty:
                flash('All employees have voted!', 'success')
                return redirect(url_for('admin_results'))
            
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                not_voted_df.to_excel(writer, sheet_name='Not Voted Employees', index=False)
            
            output.seek(0)
            filename = f"not_voted_employees_{datetime.now().strftime('%Y%m%d')}.xlsx"
        
        else:
            flash('Invalid report type.', 'error')
            return redirect(url_for('admin_results'))
        
        return send_file(output, 
                        download_name=filename,
                        as_attachment=True,
                        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    except Exception as e:
        flash(f'Error generating report: {str(e)}', 'error')
        return redirect(url_for('admin_results'))

# --- ERROR HANDLERS ---

@app.errorhandler(404)
def page_not_found(e):
    """Handle 404 errors"""
    return render_template('login.html'), 404

@app.errorhandler(500)
def internal_error(e):
    """Handle 500 errors"""
    flash('An internal error occurred. Please try again.', 'error')
    return redirect(url_for('index'))

# --- INITIALIZE & RUN ---

try:
    init_db()
except Exception as e:
    print(f"Database initialization error: {e}")

if __name__ == '__main__':
    app.run(debug=False)
