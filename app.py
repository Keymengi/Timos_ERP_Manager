import re
import csv
import os
import io
import secrets
import atexit
from functools import wraps
from flask import Response
from flask import Flask, render_template, request, redirect, flash, url_for, jsonify
from datetime import datetime, timedelta, timezone
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_migrate import Migrate
from models import db, get_eat_time, User, Customer, Debt, Payment, Product, Sale, SaleItem, ReturnItem, Quotation, ServiceBooking, ToolLoan, SMSLog
from apscheduler.schedulers.background import BackgroundScheduler
from mobitech_service import send_sms

app = Flask(__name__)

# -------------------
# Configuration
# -------------------
database_url = os.environ.get('DATABASE_URL')

# --- SECRET_KEY: loaded strictly from the environment, never hardcoded ---
# database_url being set is our signal that this is the live/production
# deployment (Render, with a real Postgres instance attached) rather than
# a local dev run against SQLite. In that case a missing SECRET_KEY is a
# hard error — starting up with no key (or a guessable one) would let
# anyone forge session cookies. Locally, we fall back to a random key
# generated fresh each run, so dev keeps working without extra setup;
# the only cost is that logging in again is needed after every restart,
# since sessions signed with the old random key stop validating.
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')
if not app.config['SECRET_KEY']:
    if database_url:
        raise RuntimeError(
            "SECRET_KEY environment variable is not set. Set it in your Render "
            "environment variables before deploying — do not fall back to a "
            "hardcoded value in code."
        )
    app.config['SECRET_KEY'] = secrets.token_hex(32)
    print(
        "[WARNING] SECRET_KEY not set — using a random development-only key. "
        "Sessions will not persist across restarts. Set SECRET_KEY in your "
        "environment for a stable key (required in production)."
    )

if database_url:
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///timos_erp_v2.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# --- Connection pool resilience ---
# Render's managed Postgres silently closes connections that sit idle for a
# while. Without these options, SQLAlchemy keeps handing out those now-dead
# connections from its pool, which surfaces as cryptic errors like
# "SSL error: decryption failed or bad record mac" or "SSL SYSCALL error:
# EOF detected" — exactly what was crashing the reminder scheduler and the
# /bookings login lookup. pool_pre_ping does a cheap liveness check (and
# transparently reconnects) before each checkout; pool_recycle proactively
# retires connections before Render's idle timeout gets to them. SQLite
# (local dev) doesn't need or support these, so only apply for Postgres.
if database_url:
    app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
        'pool_pre_ping': True,
        'pool_recycle': 280,
    }

db.init_app(app)

# -------------------
# Flask-Migrate
# -------------------
# Lets future schema changes be applied with `flask db migrate` / `flask db
# upgrade` instead of editing the database by hand or (worse) deleting and
# recreating it. See MIGRATIONS.md for the one-time setup and day-to-day
# commands. This coexists with the ad-hoc column-upgrade block near the
# bottom of this file, which stays in place so existing deployments that
# predate Flask-Migrate keep working — new schema changes going forward
# should go through a migration instead of being added to that block.
migrate = Migrate(app, db)

# -------------------
# Flask-Login & RBAC Setup
# -------------------
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'danger'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_user.role != 'Admin':
            flash("Access Denied: You need Admin privileges to view this page.", "danger")
            return redirect(url_for('home'))
        return f(*args, **kwargs)
    return decorated_function

# --- Currency Filter ---
@app.template_filter('currency')
def currency_format(value):
    if value is None:
        return "KSh 0.00"
    return f"KSh {value:,.2f}"

# --- Quantity/Stock Filter ---
# Formats numbers that are now allowed to have decimals (e.g. 2.5 metres of
# cable) so a whole number still displays as "50" rather than "50.0".
@app.template_filter('qty')
def qty_format(value):
    if value is None:
        return "0"
    value = float(value)
    if value == int(value):
        return f"{int(value):,}"
    return f"{value:,.2f}".rstrip('0').rstrip('.')

# Plain-Python version of the same formatting, for use inside flash messages
# (which aren't run through Jinja filters).
def fmt_qty(value):
    return qty_format(value)

# --- CSV Export Helper ---
# Centralizes CSV generation for every "Export CSV" button on the Analytics
# page. Two things this fixes versus building CSV rows with manual
# f-string/comma joins (as the old inventory-only export did):
#
#   1. Correctness: a customer or product name containing a comma, quote,
#      or newline would silently corrupt a hand-built CSV row. Python's
#      csv module quotes/escapes fields properly so the file always opens
#      cleanly in Excel/Sheets.
#   2. CSV formula injection: a cell value that starts with =, +, - or @
#      is interpreted as a formula by Excel/Sheets when the file is
#      opened, which is a known way to smuggle in a malicious formula via
#      user-entered data (e.g. a customer name typed as "=cmd|...").
#      _safe_cell prefixes such values with a leading apostrophe so
#      spreadsheet apps display them as plain text instead of executing
#      them.
def _safe_cell(value):
    text = "" if value is None else str(value)
    if text and text[0] in ("=", "+", "-", "@"):
        return "'" + text
    return text

def make_csv_response(filename, header, rows):
    """
    header: list of column names.
    rows: iterable of iterables (one per row), same length as header.
    Returns a Flask Response with the right CSV headers, ready to `return`
    directly from a route.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    for row in rows:
        writer.writerow([_safe_cell(v) for v in row])

    return Response(
        buffer.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )

# --- Real-Time Gross Profit Injector ---
@app.context_processor
def inject_today_profit():
    """Calculates daily gross profit across all templates."""
    if not current_user.is_authenticated:
        return dict(today_gross_profit=0.0)
    
    today_start = get_eat_time().replace(hour=0, minute=0, second=0, microsecond=0)
    sales_today = Sale.query.filter(Sale.date >= today_start).all()
    
    daily_profit = 0
    for sale in sales_today:
        if sale.status != "Cancelled":
            for item in sale.items:
                returned_qty = db.session.query(func.sum(ReturnItem.quantity)).filter_by(
                    sale_id=sale.id, product_id=item.product_id).scalar() or 0
                effective_qty = item.quantity - returned_qty
                
                if effective_qty > 0:
                    daily_profit += (item.price - item.product.purchase_price) * effective_qty
                    
    return dict(today_gross_profit=daily_profit)

# -------------------
# Technician contact — reminders about upcoming bookings also go to this
# person, so they know a job is coming up. Change these two lines if the
# technician ever changes.
# -------------------
TECHNICIAN_NAME = "Samuel Githae"
TECHNICIAN_PHONE = "0721276345"

# -------------------
# Background Job: Reminders
# -------------------
def process_reminders():
    """Background task checking for booked appointments & active debts"""
    with app.app_context():
        now = get_eat_time()

        # 1. Appointment Reminders — each booking has its own custom lead
        # time (reminder_lead_hours), set individually when it was created,
        # instead of a fixed 24 hours for every booking. Fires once per
        # booking, to both the customer and the technician.
        upcoming_bookings = ServiceBooking.query.filter(
            ServiceBooking.booking_date > now,
            ServiceBooking.reminder_sent == False,
            ServiceBooking.status.in_(['Pending', 'Confirmed'])
        ).all()

        for b in upcoming_bookings:
            lead_hours = b.reminder_lead_hours if b.reminder_lead_hours is not None else 24
            if (b.booking_date - now) > timedelta(hours=lead_hours):
                continue  # Not yet within this booking's own reminder window

            cust = b.customer
            cust_name = cust.name if cust else "Customer"
            phone = cust.contact_info if cust else None
            when_str = b.booking_date.strftime('%d %b %Y, %I:%M %p')

            # -- Customer's reminder --
            customer_message = (
                f"Hi {cust_name}, reminder from Timos: your '{b.service_name}' appointment is "
                f"scheduled for {when_str}, handled by {TECHNICIAN_NAME}. See you then!"
            )
            success, status_label, error_detail = send_sms(phone, customer_message)
            db.session.add(SMSLog(
                recipient_name=cust_name, phone_number=phone, message=customer_message,
                category="Booking Reminder", channel="SMS", status=status_label,
                error_detail=error_detail, date_sent=get_eat_time()
            ))
            print(f"[REMINDER] Upcoming Service Booking: {cust_name} at {b.booking_date.strftime('%Y-%m-%d %H:%M')} — SMS {status_label}")

            # -- Technician's reminder --
            tech_message = (
                f"Hi {TECHNICIAN_NAME}, reminder: you have a '{b.service_name}' appointment with "
                f"{cust_name} scheduled for {when_str}."
            )
            tech_success, tech_status_label, tech_error_detail = send_sms(TECHNICIAN_PHONE, tech_message)
            db.session.add(SMSLog(
                recipient_name=TECHNICIAN_NAME, phone_number=TECHNICIAN_PHONE, message=tech_message,
                category="Technician Reminder", channel="SMS", status=tech_status_label,
                error_detail=tech_error_detail, date_sent=get_eat_time()
            ))
            print(f"[REMINDER] Technician notified for booking with {cust_name} — SMS {tech_status_label}")

            b.reminder_sent = True

        # 2. Debt Reminders — repeats every 24 hours for as long as the debt
        # stays Active, instead of sending just once. Tracks the last time
        # each debt was reminded about (last_reminder_sent) rather than a
        # one-shot yes/no flag.
        active_debts = Debt.query.filter(Debt.status == 'Active').all()

        for d in active_debts:
            last_sent = d.last_reminder_sent or d.date_taken
            if (now - last_sent) < timedelta(hours=24):
                continue  # Not due for another reminder yet

            cust = d.customer
            phone = cust.contact_info if cust else None

            message = (
                f"Hi {d.customer_name}, this is a reminder from Timos that you have an outstanding "
                f"balance of KSh {d.balance:,.2f}. Kindly clear at your earliest convenience. Thank you!"
            )
            success, status_label, error_detail = send_sms(phone, message)

            db.session.add(SMSLog(
                recipient_name=d.customer_name, phone_number=phone, message=message,
                category="Debt Reminder", channel="SMS", status=status_label,
                error_detail=error_detail, date_sent=get_eat_time()
            ))

            print(f"[REMINDER] Overdue Debt: Ksh {d.balance} owed by {d.customer_name} requires follow-up — SMS {status_label}")
            d.last_reminder_sent = now

        db.session.commit()

# Init Scheduler
scheduler = BackgroundScheduler()
scheduler.add_job(func=process_reminders, trigger="interval", minutes=1)
scheduler.start()
atexit.register(lambda: scheduler.shutdown())


# -------------------
# Authentication Routes
# -------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect("/")

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            flash(f"Welcome back, {user.username}!", "success")
            next_page = request.args.get('next')
            return redirect(next_page or "/")
        else:
            flash("Invalid username or password.", "danger")

    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "success")
    return redirect("/login")

# -------------------
# Application Routes (Protected)
# -------------------
@app.route("/")
@login_required
def home():
    return render_template("home.html") 

# 1. CUSTOMERS
@app.route("/customers", methods=["GET", "POST"])
@login_required
@admin_required
def customers():
    if request.method == "POST":
        data = request.get_json() if request.is_json else request.form
        
        name = data.get("name", "").strip()
        contact_info = data.get("contact_info", "").strip()

        if not re.match(r"^[A-Za-z\s]+$", name):
            flash("Error: Customer name must contain only letters and spaces.", "danger")
            return redirect("/customers")
            
        if len(contact_info) < 10:
            flash("Error: Contact info must be at least 10 characters/numbers long.", "danger")
            return redirect("/customers")

        try:
            new_customer = Customer(name=name, contact_info=contact_info)
            db.session.add(new_customer)
            db.session.commit()
            flash("Customer added successfully!", "success")
        except IntegrityError:
            db.session.rollback()
            flash(f"Error: Customer name '{name}' already exists!", "danger")
            
        return redirect("/customers")

    search_query = request.args.get("search", "").strip()
    if search_query:
        sq_no_space = search_query.replace(" ", "")
        all_customers = Customer.query.filter(func.replace(Customer.name, ' ', '').ilike(f"%{sq_no_space}%")).all()
    else:
        all_customers = Customer.query.all()
        
    return render_template("customers.html", customers=all_customers, search_query=search_query)

@app.route("/customers/edit/<int:pid>", methods=["POST"])
@login_required
@admin_required
def edit_customer(pid):
    customer = Customer.query.get_or_404(pid)
    data = request.get_json() if request.is_json else request.form
    
    name = data.get("name", "").strip()
    contact_info = data.get("contact_info", "").strip()

    if not re.match(r"^[A-Za-z\s]+$", name):
        flash("Error: Customer name must contain only letters and spaces.", "danger")
        return redirect("/customers")

    customer.name = name
    customer.contact_info = contact_info
    
    try:
        db.session.commit()
        flash("Customer updated successfully!", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Error: That name is already taken by another customer.", "danger")
        
    return redirect("/customers")

# 2. DEBTS
@app.route("/debts", methods=["GET", "POST"])
@login_required
def debts():
    if request.method == "POST":
        customer_id = request.form.get("customer_id")
        new_customer_name = request.form.get("new_customer_name", "").strip()
        
        try:
            amount = float(request.form.get("amount", 0))
        except ValueError:
            amount = 0

        if amount <= 0:
            flash("Error: Debt amount must be greater than zero.", "danger")
            return redirect("/debts")

        if new_customer_name:
            cust = Customer.query.filter_by(name=new_customer_name).first()
            if not cust:
                cust = Customer(name=new_customer_name, contact_info="N/A")
                db.session.add(cust)
                db.session.flush()
            customer_id = cust.pid
            
        if not customer_id:
            flash("Error: Please select an existing customer or enter a new customer name.", "danger")
            return redirect("/debts")

        customer_obj = Customer.query.get(customer_id)
        
        active_debt = Debt.query.filter_by(customer_id=customer_id, status="Active").first()
        if active_debt:
            active_debt.amount += amount
            active_debt.balance += amount
            flash(f"Debt appended! Added KSh {amount} to {customer_obj.name}'s active debt.", "success")
        else:
            new_debt = Debt(
                customer_id=customer_id, 
                customer_name=customer_obj.name, 
                amount=amount, 
                balance=amount,
                date_taken=get_eat_time()
            )
            db.session.add(new_debt)
            flash("New debt recorded successfully!", "success")
            
        db.session.commit()
        return redirect("/debts")
    
    all_debts = Debt.query.order_by(Debt.date_taken.desc()).all()
    customers = Customer.query.all()
    return render_template("debts.html", debts=all_debts, customers=customers)

@app.route("/debts/edit/<int:id>", methods=["POST"])
@login_required
def edit_debt(id):
    debt = Debt.query.get_or_404(id)
    try:
        new_balance = float(request.form.get("balance", debt.balance))
    except ValueError:
        new_balance = debt.balance

    if new_balance < 0:
        flash("Balance cannot be negative.", "danger")
        return redirect("/debts")
    
    debt.balance = new_balance
    if debt.balance <= 0:
        debt.status = "Cleared"
    else:
        debt.status = "Active"
        
    db.session.commit()
    flash("Debt balance updated successfully.", "success")
    return redirect("/debts")

@app.route("/debts/delete/<int:id>", methods=["POST"])
@login_required
def delete_debt(id):
    debt = Debt.query.get_or_404(id)
    db.session.delete(debt)
    db.session.commit()
    flash(f"Debt record for {debt.customer_name} permanently deleted.", "success")
    return redirect("/debts")

# 3. PAYMENTS
@app.route("/payments", methods=["GET", "POST"])
@login_required
def payments():
    if request.method == "POST":
        debt_id = request.form.get("debt_id")
        
        try:
            amount = float(request.form.get("amount", 0))
        except ValueError:
            amount = 0

        debt = Debt.query.get(debt_id)
        
        if amount <= 0:
            flash("Error: Payment amount must be greater than zero.", "danger")
            return redirect("/payments")
            
        if amount > debt.balance:
            flash("Error: Payment cannot exceed outstanding balance.", "danger")
            return redirect("/payments")

        new_payment = Payment(debt_id=debt_id, amount=amount, date=get_eat_time())
        db.session.add(new_payment)

        debt.balance -= amount
        if debt.balance <= 0:
            debt.status = "Cleared"

        db.session.commit()
        flash("Payment recorded successfully!", "success")
        return redirect("/payments")

    debts = Debt.query.filter_by(status="Active").all()
    payments = Payment.query.order_by(Payment.date.desc()).all()
    return render_template("payments.html", debts=debts, payments=payments)

@app.route("/payments/delete/<int:id>", methods=["POST"])
@login_required
def delete_payment(id):
    payment = Payment.query.get_or_404(id)
    debt = payment.debt
    
    # Restore Debt Balance
    debt.balance += payment.amount
    if debt.balance > 0 and debt.status == "Cleared":
        debt.status = "Active"
        
    db.session.delete(payment)
    db.session.commit()
    flash(f"Payment deleted and Ksh {payment.amount} returned to {debt.customer_name}'s balance.", "success")
    return redirect("/payments")

# 4. INVENTORY & RETURNS (Unchanged bulk operations)
@app.route("/inventory", methods=["GET", "POST"])
@login_required
@admin_required
def inventory():
    if request.method == "POST":
        data = request.get_json() if request.is_json else request.form
        
        raw_name = data.get("name", "").strip()
        try:
            purchase_price = float(data.get("purchase_price", 0))
            selling_price = float(data.get("selling_price", 0))
            added_stock = float(data.get("stock", 0))
        except ValueError:
            flash("Error: Invalid numeric input.", "danger")
            return redirect("/inventory")

        if added_stock <= 0:
            flash("Error: Restock quantity must be greater than zero.", "danger")
            return redirect("/inventory")

        min_sp = purchase_price + purchase_price * 0.5
        # Note: selling price is no longer required to be above min_sp.
        # min_sp is still calculated and stored (so it keeps showing
        # correctly on the Inventory page) — it just doesn't block saving.

        existing_product = Product.query.filter(Product.name.ilike(raw_name)).first()

        if existing_product:
            existing_product.stock += added_stock
            existing_product.purchase_price = purchase_price
            existing_product.selling_price = selling_price
            existing_product.min_selling_price = min_sp
            db.session.commit()
            flash(f"Restocked! Added {fmt_qty(added_stock)} units to '{existing_product.name}'.", "success")
        else:
            formatted_name = raw_name.title()
            try:
                new_product = Product(
                    name=formatted_name, purchase_price=purchase_price, min_selling_price=min_sp,
                    selling_price=selling_price, stock=added_stock
                )
                db.session.add(new_product)
                db.session.commit()
                flash(f"New product '{formatted_name}' added to inventory!", "success")
            except IntegrityError:
                db.session.rollback()
                flash("Error: Product creation conflict.", "danger")

        return redirect("/inventory")

    # Search, stock-status filtering and sorting all now happen client-side
    # in the browser (see the <script> block in inventory.html) so the
    # inventory page keeps working — search bar, sort, everything — even
    # with no internet connection. This route just hands over every
    # product, once, and the JS on the page slices/reorders it from there.
    products = Product.query.order_by(Product.name.asc()).all()
    all_products = products
    total_valuation = sum(p.purchase_price * p.stock for p in all_products)

    return render_template("inventory.html", products=products, all_products=all_products, total_valuation=total_valuation)

@app.route("/inventory/edit/<int:id>", methods=["POST"])
@login_required
@admin_required
def edit_inventory(id):
    product = Product.query.get_or_404(id)
    data = request.get_json() if request.is_json else request.form
    
    product.name = data.get("name", product.name).strip().title()
    try:
        product.purchase_price = float(data.get("purchase_price", product.purchase_price))
        product.selling_price = float(data.get("selling_price", product.selling_price))
        product.stock = float(data.get("stock", product.stock))
    except ValueError:
        pass
        
    product.min_selling_price = product.purchase_price + (product.purchase_price * 0.5)
    
    try:
        db.session.commit()
        flash("Product updated securely.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Update failed. Ensure product name is unique.", "danger")
    return redirect("/inventory")

@app.route("/inventory/delete/<int:id>", methods=["POST"])
@login_required
@admin_required
def delete_inventory(id):
    product = Product.query.get_or_404(id)
    try:
        db.session.delete(product)
        db.session.commit()
        flash("Product deleted securely.", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Cannot delete product because it exists in past transaction logs. Consider editing stock to 0 instead.", "danger")
    return redirect("/inventory")

@app.route("/inventory/import", methods=["POST"])
@login_required
@admin_required
def import_inventory():
    file = request.files.get("file")
    if not file or file.filename == "":
        flash("Error: No file was selected.", "danger")
        return redirect("/inventory")

    if not file.filename.lower().endswith(".csv"):
        flash("Error: Please upload a .csv file.", "danger")
        return redirect("/inventory")

    try:
        stream = io.StringIO(file.stream.read().decode("utf-8-sig"), newline=None)
        reader = csv.DictReader(stream)
    except Exception:
        flash("Error: Couldn't read that file. Make sure it's a valid CSV.", "danger")
        return redirect("/inventory")

    # Matches the columns produced by the "Export CSV" button, so a file
    # exported from this app can always be re-imported unchanged.
    required_columns = {"Product Name", "Purchase Price", "Selling Price", "Stock"}
    if not reader.fieldnames or not required_columns.issubset(set(reader.fieldnames)):
        flash(
            "Error: That CSV is missing required columns. It needs: Product Name, Purchase Price, "
            "Selling Price, Stock. Tip: use the 'Export CSV' button first to see the exact format expected.",
            "danger"
        )
        return redirect("/inventory")

    added_count = 0
    restocked_count = 0
    skipped_rows = []

    for row_num, row in enumerate(reader, start=2):  # row 1 is the header
        raw_name = (row.get("Product Name") or "").strip()
        if not raw_name:
            skipped_rows.append(f"Row {row_num}: missing product name")
            continue

        try:
            purchase_price = float(row.get("Purchase Price", 0))
            selling_price = float(row.get("Selling Price", 0))
            stock = float(row.get("Stock", 0))
        except (ValueError, TypeError):
            skipped_rows.append(f"Row {row_num} ('{raw_name}'): one of the numbers isn't valid")
            continue

        min_sp = purchase_price + purchase_price * 0.5
        # Note: unlike the manual "add product" form, imported rows are NOT
        # rejected for having a selling price below min_sp. The minimum is
        # still calculated and stored on the product (so it still shows
        # correctly everywhere else in the app) — it just doesn't block
        # the import itself.

        existing_product = Product.query.filter(Product.name.ilike(raw_name)).first()
        if existing_product:
            existing_product.stock += stock
            existing_product.purchase_price = purchase_price
            existing_product.selling_price = selling_price
            existing_product.min_selling_price = min_sp
            restocked_count += 1
        else:
            new_product = Product(
                name=raw_name.title(), purchase_price=purchase_price, min_selling_price=min_sp,
                selling_price=selling_price, stock=stock
            )
            db.session.add(new_product)
            added_count += 1

    db.session.commit()

    summary = f"Import complete: {added_count} new product(s) added, {restocked_count} restocked."
    if skipped_rows:
        shown = "; ".join(skipped_rows[:5])
        more = f" (+{len(skipped_rows) - 5} more)" if len(skipped_rows) > 5 else ""
        flash(f"{summary} Skipped {len(skipped_rows)} row(s): {shown}{more}", "warning")
    else:
        flash(summary, "success")

    return redirect("/inventory")

@app.route("/return_item", methods=["POST"])
@login_required
@admin_required
def return_item():
    data = request.get_json() if request.is_json else request.form
    
    sale_id = data.get("sale_id")
    product_id = data.get("product_id")
    
    try:
        return_qty = float(data.get("return_qty", 0))
    except ValueError:
        return_qty = 0

    if return_qty <= 0:
        flash("Error: Return quantity must be greater than zero.", "danger")
        return redirect(request.referrer or "/reports")

    sale_item = SaleItem.query.filter_by(sale_id=sale_id, product_id=product_id).first()
    if not sale_item:
        flash("Error: Item not found in this transaction.", "danger")
        return redirect(request.referrer or "/reports")

    returned_already = db.session.query(func.sum(ReturnItem.quantity)).filter_by(sale_id=sale_id, product_id=product_id).scalar() or 0
    available_to_return = sale_item.quantity - returned_already

    if return_qty > available_to_return:
        flash(f"Error: Cannot return {fmt_qty(return_qty)} units. Only {fmt_qty(available_to_return)} unreturned units remain.", "danger")
        return redirect(request.referrer or "/reports")
        
    ret = ReturnItem(sale_id=sale_id, product_id=product_id, quantity=return_qty, date_returned=get_eat_time())
    db.session.add(ret)

    product = Product.query.get(product_id)
    product.stock += return_qty

    refund_amount = return_qty * sale_item.price
    sale = Sale.query.get(sale_id)
    sale.total_amount -= refund_amount

    # Auto-handle debts
    if sale.sale_type == "Debt" and sale.customer_id:
        active_debt = Debt.query.filter_by(customer_id=sale.customer_id, status="Active").first()
        if active_debt:
            active_debt.amount -= refund_amount
            active_debt.balance -= refund_amount
            if active_debt.balance <= 0:
                active_debt.balance = 0
                active_debt.status = "Cancelled"

    db.session.commit()
    flash(f"Successfully returned {return_qty}x {product.name}.", "success")
    return redirect(request.referrer or "/reports")

# 5. SALES
@app.route("/sales", methods=["GET", "POST"])
@login_required
def sales():
    if request.method == "POST":
        customer_id = request.form.get("customer_id")
        new_customer_name = request.form.get("new_customer_name")
        product_id = request.form.get("product_id")
        sale_type = request.form.get("sale_type")
        custom_price_str = request.form.get("custom_price")
        
        try:
            quantity = float(request.form.get("quantity", 0))
        except ValueError:
            quantity = 0

        if quantity <= 0:
            flash("Error: Sale quantity must be greater than zero.", "danger")
            return redirect("/sales")

        product = Product.query.get(product_id)
        if quantity > product.stock:
            flash(f"Error: Only {fmt_qty(product.stock)} left in stock for {product.name}.", "danger")
            return redirect("/sales")

        if new_customer_name:
            cust = Customer.query.filter_by(name=new_customer_name).first()
            if not cust:
                cust = Customer(name=new_customer_name)
                db.session.add(cust)
                db.session.flush()
            customer_id = cust.pid

        if not customer_id:
            customer_id = None
            if sale_type == "Debt":
                flash("Error: You cannot record a Debt for an unknown walk-in customer.", "danger")
                return redirect("/sales")

        # Custom Pricing Override
        if custom_price_str and custom_price_str.strip():
            try:
                unit_price = float(custom_price_str)
            except ValueError:
                unit_price = product.selling_price
        else:
            unit_price = product.selling_price

        total_price = unit_price * quantity

        new_sale = Sale(customer_id=customer_id, total_amount=total_price, sale_type=sale_type, date=get_eat_time())
        db.session.add(new_sale)
        db.session.flush()

        sale_item = SaleItem(sale_id=new_sale.id, product_id=product_id, quantity=quantity, price=unit_price)
        db.session.add(sale_item)
        product.stock -= quantity

        # Log Debt
        if sale_type == "Debt":
            active_debt = Debt.query.filter_by(customer_id=customer_id, status="Active").first()
            customer_obj = Customer.query.get(customer_id)
            cust_name = customer_obj.name if customer_obj else "Unknown"

            if active_debt:
                active_debt.amount += total_price
                active_debt.balance += total_price
            else:
                new_debt = Debt(
                    customer_id=customer_id, customer_name=cust_name, amount=total_price, 
                    balance=total_price, date_taken=get_eat_time()
                )
                db.session.add(new_debt)

        db.session.commit()
        flash(f"Sale recorded successfully as {sale_type}!", "success")
        return redirect("/sales")

    customers = Customer.query.all()
    products = Product.query.filter(Product.stock > 0).all()
    sales_list = Sale.query.order_by(Sale.date.desc()).limit(50).all()
    
    return render_template("sales.html", customers=customers, products=products, sales=sales_list, ReturnItem=ReturnItem, func=func, db=db)

@app.route("/sales/cancel/<int:sale_id>", methods=["POST"])
@login_required
def cancel_sale(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    
    if sale.status == "Cancelled":
        flash("This sale has already been cancelled.", "warning")
        return redirect(request.referrer or "/sales")

    sale.status = "Cancelled"
    
    for item in sale.items:
        returned_qty = db.session.query(func.sum(ReturnItem.quantity)).filter_by(sale_id=sale.id, product_id=item.product_id).scalar() or 0
        unreturned_qty = item.quantity - returned_qty
        
        if unreturned_qty > 0:
            product = Product.query.get(item.product_id)
            if product:
                product.stock += unreturned_qty
                
    # Auto Cancel associated active Debt balances if debt-based
    if sale.sale_type == "Debt" and sale.customer_id:
        active_debt = Debt.query.filter_by(customer_id=sale.customer_id, status="Active").first()
        if active_debt:
            active_debt.amount -= sale.total_amount
            active_debt.balance -= sale.total_amount
            if active_debt.balance <= 0:
                active_debt.balance = 0
                active_debt.status = "Cancelled"
            
    db.session.commit()
    flash(f"Sale #{sale.id} fully cancelled and reversed.", "success")
    return redirect(request.referrer or "/sales")

@app.route("/sales/delete/<int:sale_id>", methods=["POST"])
@login_required
def delete_sale(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    
    # Process reversion if the sale wasn't officially 'Cancelled' yet
    if sale.status != "Cancelled":
        for item in sale.items:
            returned_qty = db.session.query(func.sum(ReturnItem.quantity)).filter_by(sale_id=sale.id, product_id=item.product_id).scalar() or 0
            unreturned_qty = item.quantity - returned_qty
            
            if unreturned_qty > 0:
                product = Product.query.get(item.product_id)
                if product:
                    product.stock += unreturned_qty

        # Adjust Debt Auto-cancellation
        if sale.sale_type == "Debt" and sale.customer_id:
            active_debt = Debt.query.filter_by(customer_id=sale.customer_id, status="Active").first()
            if active_debt:
                active_debt.amount -= sale.total_amount
                active_debt.balance -= sale.total_amount
                if active_debt.balance <= 0:
                    active_debt.balance = 0
                    active_debt.status = "Cancelled"

    db.session.delete(sale)
    db.session.commit()
    flash(f"Sale #{sale_id} record physically deleted.", "success")
    return redirect(request.referrer or "/sales")


# 6. QUOTATIONS
@app.route("/quotations", methods=["GET", "POST"])
@login_required
@admin_required
def quotations():
    if request.method == "POST":
        customer_id = request.form.get("customer_id")
        amount = float(request.form.get("amount", 0))
        valid_days = int(request.form.get("valid_days", 7))
        
        if amount <= 0:
            flash("Error: Amount must be greater than zero.", "danger")
            return redirect("/quotations")
            
        valid_until = get_eat_time() + timedelta(days=valid_days)
        new_quote = Quotation(customer_id=customer_id, total_amount=amount, date=get_eat_time(), valid_until=valid_until)
        db.session.add(new_quote)
        db.session.commit()
        flash("Quotation saved successfully!", "success")
        return redirect("/quotations")

    customers = Customer.query.all()
    quotations = Quotation.query.all()
    return render_template("quotations.html", customers=customers, quotations=quotations)

# 7. REPORTS
@app.route("/reports", methods=["GET", "POST"])
@login_required
@admin_required
def reports():
    end_date_dt = get_eat_time()
    start_date_dt = end_date_dt - timedelta(days=7)

    customer_id = None
    if request.method == "POST":
        customer_id = request.form.get("customer_id")
        req_start = request.form.get("start_date")
        req_end = request.form.get("end_date")
        
        if req_start:
            start_date_dt = datetime.strptime(req_start, "%Y-%m-%d")
        if req_end:
            end_date_dt = datetime.strptime(req_end, "%Y-%m-%d").replace(hour=23, minute=59, second=59)

    query_debts = Debt.query.filter(Debt.status == "Active")
    if customer_id:
        query_debts = query_debts.filter(Debt.customer_id == customer_id)
    outstanding_debts = query_debts.all()

    query_payments = Payment.query.filter(Payment.date.between(start_date_dt, end_date_dt))
    if customer_id:
        query_payments = query_payments.join(Debt).filter(Debt.customer_id == customer_id)
    total_payments = sum(p.amount for p in query_payments.all())

    query_sales = Sale.query.filter(Sale.date.between(start_date_dt, end_date_dt))
    if customer_id:
        query_sales = query_sales.filter(Sale.customer_id == customer_id)
    
    total_sales = 0
    profit = 0
    
    for sale in query_sales.all():
        if sale.status != "Cancelled":
            total_sales += sale.total_amount
            for item in sale.items:
                returned_qty = db.session.query(func.sum(ReturnItem.quantity)).filter_by(sale_id=sale.id, product_id=item.product_id).scalar() or 0
                effective_qty = item.quantity - returned_qty
                
                if effective_qty > 0:
                    profit += (item.price - item.product.purchase_price) * effective_qty

    customers = Customer.query.all()
    recent_sms = SMSLog.query.order_by(SMSLog.date_sent.desc()).limit(20).all()

    return render_template(
        "reports.html", outstanding_debts=outstanding_debts, total_payments=total_payments,
        total_sales=total_sales, profit=profit, customers=customers,
        start_date=start_date_dt.strftime("%Y-%m-%d"), end_date=end_date_dt.strftime("%Y-%m-%d"),
        sales_list=query_sales.all(), ReturnItem=ReturnItem, func=func, db=db,
        recent_sms=recent_sms
    )

@app.route("/sms/test", methods=["POST"])
@login_required
@admin_required
def send_test_sms():
    """Sends a one-off SMS to a phone number typed in on the Reports page,
    so you can confirm the Mobitech integration is wired up correctly
    (live send, or console-mode print if MOBITECH_API_KEY/MOBITECH_SENDER_NAME
    aren't set yet) without waiting for a real debt/booking reminder to fire."""
    phone = request.form.get("test_phone", "").strip()
    if not phone:
        flash("Error: Enter a phone number to send the test SMS to.", "danger")
        return redirect("/reports")

    message = f"Timos ERP test message, sent by {current_user.username} to confirm SMS is working."
    success, status_label, error_detail = send_sms(phone, message)

    db.session.add(SMSLog(
        recipient_name=f"Test ({current_user.username})", phone_number=phone, message=message,
        category="Test", channel="SMS", status=status_label,
        error_detail=error_detail, date_sent=get_eat_time()
    ))
    db.session.commit()

    if status_label == "Sent":
        flash(f"Test SMS sent to {phone}.", "success")
    elif status_label == "Console":
        flash(f"MOBITECH_API_KEY/MOBITECH_SENDER_NAME not set — test message for {phone} was printed to the server console instead of sent.", "warning")
    else:
        flash(f"Test SMS to {phone} failed: {error_detail or 'unknown error'}", "danger")

    return redirect("/reports")

@app.route("/analytics")
@login_required
@admin_required
def analytics():
    return render_template("analytics.html")

@app.route("/export/inventory")
@login_required
@admin_required
def export_inventory():
    products = Product.query.order_by(Product.name.asc()).all()
    header = ["ID", "Product Name", "Purchase Price", "Minimum S.P", "Selling Price", "Stock"]
    rows = (
        [p.id, p.name, p.purchase_price, p.min_selling_price, p.selling_price, p.stock]
        for p in products
    )
    return make_csv_response("inventory_report.csv", header, rows)

@app.route("/export/customers")
@login_required
@admin_required
def export_customers():
    customers = Customer.query.order_by(Customer.name.asc()).all()
    header = ["ID", "Name", "Contact Info"]
    rows = ([c.pid, c.name, c.contact_info] for c in customers)
    return make_csv_response("customers_report.csv", header, rows)

@app.route("/export/sales")
@login_required
@admin_required
def export_sales():
    sales = Sale.query.order_by(Sale.date.desc()).all()
    header = ["Sale ID", "Date", "Customer", "Items", "Total Amount", "Sale Type", "Status"]

    def generate_rows():
        for s in sales:
            items_str = "; ".join(
                f"{fmt_qty(item.quantity)}x {item.product.name}" for item in s.items
            )
            yield [
                s.id, s.date.strftime('%Y-%m-%d %H:%M'),
                s.customer.name if s.customer else "Walk-in",
                items_str, s.total_amount, s.sale_type, s.status
            ]

    return make_csv_response("sales_report.csv", header, generate_rows())

@app.route("/export/debts")
@login_required
@admin_required
def export_debts():
    debts = Debt.query.order_by(Debt.date_taken.desc()).all()
    header = ["ID", "Customer", "Amount", "Balance", "Status", "Date Taken"]
    rows = (
        [d.id, d.customer_name, d.amount, d.balance, d.status, d.date_taken.strftime('%Y-%m-%d %H:%M')]
        for d in debts
    )
    return make_csv_response("debts_report.csv", header, rows)

@app.route("/export/payments")
@login_required
@admin_required
def export_payments():
    payments = Payment.query.order_by(Payment.date.desc()).all()
    header = ["ID", "Customer", "Amount", "Date"]
    rows = (
        [p.id, p.debt.customer_name if p.debt else "N/A", p.amount, p.date.strftime('%Y-%m-%d %H:%M')]
        for p in payments
    )
    return make_csv_response("payments_report.csv", header, rows)

@app.route("/export/quotations")
@login_required
@admin_required
def export_quotations():
    quotations = Quotation.query.order_by(Quotation.date.desc()).all()
    header = ["ID", "Customer", "Date", "Valid Until", "Total Amount"]
    rows = (
        [
            q.id, q.customer.name if q.customer else "N/A",
            q.date.strftime('%Y-%m-%d %H:%M') if q.date else "",
            q.valid_until.strftime('%Y-%m-%d %H:%M') if q.valid_until else "",
            q.total_amount
        ]
        for q in quotations
    )
    return make_csv_response("quotations_report.csv", header, rows)

@app.route("/export/bookings")
@login_required
@admin_required
def export_bookings():
    all_bookings = ServiceBooking.query.order_by(ServiceBooking.booking_date.desc()).all()
    header = ["ID", "Customer", "Service", "Description", "Booking Date", "Charge", "Status"]
    rows = (
        [
            b.id, b.customer.name if b.customer else "N/A", b.service_name,
            b.description or "", b.booking_date.strftime('%Y-%m-%d %H:%M'), b.charge, b.status
        ]
        for b in all_bookings
    )
    return make_csv_response("bookings_report.csv", header, rows)

@app.route("/export/loans")
@login_required
@admin_required
def export_loans():
    all_loans = ToolLoan.query.order_by(ToolLoan.date_borrowed.desc()).all()
    header = ["ID", "Customer", "Tool Name", "Date Borrowed", "Return Date", "Status"]
    rows = (
        [
            l.id, l.customer.name if l.customer else "N/A", l.tool_name,
            l.date_borrowed.strftime('%Y-%m-%d %H:%M'), l.return_date.strftime('%Y-%m-%d'), l.status
        ]
        for l in all_loans
    )
    return make_csv_response("tool_loans_report.csv", header, rows)


# 8. SERVICE BOOKINGS
@app.route("/bookings", methods=["GET", "POST"])
@login_required
@admin_required
def bookings():
    if request.method == "POST":
        customer_id = request.form.get("customer_id")
        service_name = request.form.get("service_name").strip()
        description = request.form.get("description", "").strip()
        booking_date_str = request.form.get("booking_date")
        
        try:
            charge = float(request.form.get("charge", 0))
        except ValueError:
            charge = 0

        try:
            reminder_lead_hours = float(request.form.get("reminder_lead_hours", 24))
        except ValueError:
            reminder_lead_hours = 24

        # Booking date passed without offset, map to UTC+3
        booking_date = datetime.strptime(booking_date_str, "%Y-%m-%dT%H:%M")

        new_booking = ServiceBooking(
            customer_id=customer_id, service_name=service_name, description=description, 
            booking_date=booking_date, charge=charge, reminder_lead_hours=reminder_lead_hours
        )
        db.session.add(new_booking)
        db.session.commit()
        flash("Service booking scheduled successfully!", "success")
        return redirect("/bookings")

    customers = Customer.query.all()
    all_bookings = ServiceBooking.query.order_by(ServiceBooking.booking_date.desc()).all()
    return render_template("bookings.html", customers=customers, bookings=all_bookings)

@app.route("/bookings/edit/<int:id>", methods=["POST"])
@login_required
@admin_required
def edit_booking(id):
    booking = ServiceBooking.query.get_or_404(id)
    booking.service_name = request.form.get("service_name", booking.service_name)
    booking.description = request.form.get("description", booking.description)
    try:
        booking.charge = float(request.form.get("charge", booking.charge))
    except ValueError:
        pass
        
    date_str = request.form.get("booking_date")
    if date_str:
        booking.booking_date = datetime.strptime(date_str, "%Y-%m-%dT%H:%M")

    try:
        booking.reminder_lead_hours = float(request.form.get("reminder_lead_hours", booking.reminder_lead_hours))
    except ValueError:
        pass

    db.session.commit()
    flash("Booking details updated successfully.", "success")
    return redirect("/bookings")

@app.route("/bookings/done/<int:id>", methods=["POST"])
@login_required
@admin_required
def done_booking(id):
    booking = ServiceBooking.query.get_or_404(id)
    booking.status = "Done"
    db.session.commit()
    flash("Service booking marked as Done.", "success")
    return redirect("/bookings")

@app.route("/bookings/cancel/<int:booking_id>", methods=["POST"])
@login_required
@admin_required
def cancel_booking(booking_id):
    booking = ServiceBooking.query.get_or_404(booking_id)
    if booking.status != "Cancelled":
        booking.status = "Cancelled"
        db.session.commit()
        flash(f"Service booking for '{booking.customer.name}' cancelled successfully.", "success")
    return redirect("/bookings")


# 9. TOOL LOANS
@app.route("/loans", methods=["GET", "POST"])
@login_required
def loans():
    if request.method == "POST":
        customer_id = request.form.get("customer_id")
        tool_name = request.form.get("tool_name").strip()
        return_date_str = request.form.get("return_date")

        return_date = datetime.strptime(return_date_str, "%Y-%m-%d")
        new_loan = ToolLoan(customer_id=customer_id, tool_name=tool_name, return_date=return_date)
        db.session.add(new_loan)
        db.session.commit()
        flash("Tool loan recorded successfully!", "success")
        return redirect("/loans")

    customers = Customer.query.all()
    all_loans = ToolLoan.query.order_by(ToolLoan.date_borrowed.desc()).all()
    return render_template("loans.html", customers=customers, loans=all_loans)

from flask import send_from_directory

@app.route('/sw.js')
def sw():
    response = send_from_directory('static', 'sw.js')
    response.headers['Cache-Control'] = 'no-cache'
    return response

@app.route("/loans/return/<int:loan_id>", methods=["POST"])
@login_required
def return_tool(loan_id):
    loan = ToolLoan.query.get_or_404(loan_id)
    loan.status = "Returned"
    db.session.commit()
    flash(f"Tool '{loan.tool_name}' marked as returned.", "success")
    return redirect("/loans")

# -------------------
# Database Setup & Seeding
# -------------------
with app.app_context():
    db.create_all()

    # One-time database upgrade, safe to run every time the app starts.
    #
    # This app used to store purchase price, stock, and sale/return quantities
    # as whole-numbers-only. That's now changed to allow decimals (e.g. KSh
    # 44.50, or 2.5 metres of cable). db.create_all() above only creates
    # tables that don't exist yet — it does NOT change the structure of
    # tables that already exist. So on a database that was already running
    # before this update, those columns are quietly upgraded here.
    #
    # This runs against BOTH the live Postgres database and your local
    # SQLite one — previously it only ran on Postgres, which is why your
    # local copy kept falling behind and crashing with "no such column"
    # errors every time a new field was added. Both branches are written
    # so that running them again on an already-upgraded database does
    # nothing and causes no harm.
    is_postgres = bool(database_url)

    with db.engine.connect() as conn:
        if is_postgres:
            # Postgres: fix columns that exist but with the old whole-numbers-
            # only type, now that decimals are allowed (e.g. KSh 44.50).
            column_upgrades = [
                ("product", "purchase_price"),
                ("product", "stock"),
                ("sale_item", "quantity"),
                ("return_item", "quantity"),
            ]
            for table, column in column_upgrades:
                try:
                    conn.execute(db.text(
                        f"ALTER TABLE {table} ALTER COLUMN {column} TYPE FLOAT USING {column}::float"
                    ))
                    conn.commit()
                except Exception:
                    # Already upgraded, or the table/column doesn't exist yet
                    # on a brand-new database — either way, safe to move on.
                    conn.rollback()
            # (SQLite doesn't need this step: it never enforces a column's
            # declared type strictly, so it already accepts decimals fine.)

        # Add columns that are missing entirely (rather than existing with
        # the wrong type) — this is the part that fixes today's crash.
        # Postgres supports "IF NOT EXISTS" directly; SQLite doesn't, so it
        # just tries the add and quietly ignores a "column already exists"
        # error the same way the Postgres branch ignores its own errors.
        missing_columns = [
            ("debt", "reminder_sent", "BOOLEAN DEFAULT FALSE"),
            ("service_booking", "reminder_sent", "BOOLEAN DEFAULT FALSE"),
            ("debt", "last_reminder_sent", "TIMESTAMP"),
            ("service_booking", "reminder_lead_hours", "FLOAT DEFAULT 24.0"),
            ("sms_log", "error_detail", "VARCHAR(255)"),
            # Default 'SMS' here because this column is being added retroactively —
            # any row that already existed before this update really was sent by SMS.
            # New rows explicitly pass channel="SMS" when they're inserted (see
            # mobitech_service.py), so this default only ever applies to that
            # historical backfill.
            ("sms_log", "channel", "VARCHAR(20) DEFAULT 'SMS'"),
        ]
        for table, column, col_definition in missing_columns:
            try:
                if is_postgres:
                    conn.execute(db.text(
                        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_definition}"
                    ))
                else:
                    conn.execute(db.text(
                        f"ALTER TABLE {table} ADD COLUMN {column} {col_definition}"
                    ))
                conn.commit()
            except Exception:
                conn.rollback()

    # --- Default account creation ---
    # IMPORTANT CHANGE: previously this block called set_password() on
    # EVERY startup, using a hardcoded fallback ("pass364"/"staff123") if
    # ADMIN_PASSWORD/STAFF_PASSWORD wasn't set. That meant any password you
    # changed through the app got silently overwritten back to the weak
    # hardcoded default the next time the app restarted or redeployed.
    # Now, the password is only ever set here when the account doesn't
    # exist yet (first run). After that, changing it is up to you (there's
    # no in-app "change password" screen yet — update it directly via the
    # database, or add such a screen, if you need to rotate it).
    def _create_default_user(username, role, env_var_name):
        existing = User.query.filter_by(username=username).first()
        if existing:
            return
        password = os.environ.get(env_var_name)
        if not password:
            password = secrets.token_urlsafe(12)
            print(
                f"[SECURITY] {env_var_name} is not set. Generated a one-time "
                f"password for the '{username}' account: {password}\n"
                f"           Log in and note it down now, then set {env_var_name} "
                f"in your environment so this doesn't happen again on the next restart."
            )
        new_user = User(username=username, role=role)
        new_user.set_password(password)
        db.session.add(new_user)

    _create_default_user("admin", "Admin", "ADMIN_PASSWORD")
    _create_default_user("staff", "Staff", "STAFF_PASSWORD")

    db.session.commit()

if __name__ == "__main__":
    # Debug mode (Werkzeug's interactive debugger) must never run in
    # production — it allows arbitrary code execution from the browser if
    # the app is ever exposed. It's now opt-in via FLASK_DEBUG=1, and off
    # by default. Render runs this app via gunicorn (see Procfile), which
    # never hits this block at all — this only matters for local `python
    # app.py` runs.
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")