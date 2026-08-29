import re
import csv
import os
import io
from functools import wraps
from flask import Response
from flask import Flask, render_template, request, redirect, flash, url_for, jsonify
from datetime import datetime, timedelta
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from models import db, User, Customer, Debt, Payment, Product, Sale, SaleItem, ReturnItem, Quotation, ServiceBooking, ToolLoan

app = Flask(__name__)

# -------------------
# Configuration
# -------------------
app.config['SECRET_KEY'] = 'timos_secret_key_change_in_production'

# Dynamic Database URI: Uses Render's PostgreSQL if available, otherwise falls back to local SQLite
database_url = os.environ.get('DATABASE_URL')
if database_url:
    if database_url.startswith("postgres://"):
        database_url = database_url.replace("postgres://", "postgresql://", 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///timos_erp_v2.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

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

# 1. CUSTOMERS (Admin Only)
@app.route("/customers", methods=["GET", "POST"])
@login_required
@admin_required
def customers():
    if request.method == "POST":
        data = request.get_json() if request.is_json else request.form
        
        name = data.get("name", "").strip()
        contact_info = data.get("contact_info", "").strip()

        if not re.match(r"^[A-Za-z\s]+$", name):
            if request.is_json or request.headers.get('X-Offline-Sync') == 'true':
                return jsonify({"status": "error", "message": "Customer name must contain only letters and spaces."}), 400
            flash("Error: Customer name must contain only letters and spaces.", "danger")
            return redirect("/customers")
            
        if len(contact_info) < 10:
            if request.is_json or request.headers.get('X-Offline-Sync') == 'true':
                return jsonify({"status": "error", "message": "Contact info must be at least 10 characters/numbers long."}), 400
            flash("Error: Contact info must be at least 10 characters/numbers long.", "danger")
            return redirect("/customers")

        try:
            new_customer = Customer(name=name, contact_info=contact_info)
            db.session.add(new_customer)
            db.session.commit()
            
            if request.is_json or request.headers.get('X-Offline-Sync') == 'true':
                return jsonify({"status": "success", "message": "Customer added successfully!"}), 200
                
            flash("Customer added successfully!", "success")
        except IntegrityError:
            db.session.rollback()
            if request.is_json or request.headers.get('X-Offline-Sync') == 'true':
                return jsonify({"status": "error", "message": f"Customer name '{name}' already exists!"}), 400
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
        if request.is_json or request.headers.get('X-Offline-Sync') == 'true':
            return jsonify({"status": "error", "message": "Customer name must contain only letters and spaces."}), 400
        flash("Error: Customer name must contain only letters and spaces.", "danger")
        return redirect("/customers")

    customer.name = name
    customer.contact_info = contact_info
    
    try:
        db.session.commit()
        if request.is_json or request.headers.get('X-Offline-Sync') == 'true':
            return jsonify({"status": "success", "message": "Customer updated successfully!"}), 200
        flash("Customer updated successfully!", "success")
    except IntegrityError:
        db.session.rollback()
        if request.is_json or request.headers.get('X-Offline-Sync') == 'true':
            return jsonify({"status": "error", "message": "That name is already taken."}), 400
        flash("Error: That name is already taken by another customer.", "danger")
        
    return redirect("/customers")

# 2. DEBTS (Staff + Admin)
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
                date_taken=datetime.now()
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

# 3. PAYMENTS (Staff + Admin)
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
            flash(f"Error: Payment cannot exceed outstanding balance.", "danger")
            return redirect("/payments")

        new_payment = Payment(debt_id=debt_id, amount=amount, date=datetime.now())
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

# 4. INVENTORY & RETURNS (Admin Only)
@app.route("/inventory", methods=["GET", "POST"])
@login_required
@admin_required
def inventory():
    if request.method == "POST":
        data = request.get_json() if request.is_json else request.form
        
        raw_name = data.get("name", "").strip()
        try:
            purchase_price = int(data.get("purchase_price", 0))
            selling_price = float(data.get("selling_price", 0))
            added_stock = int(data.get("stock", 0))
        except ValueError:
            if request.is_json or request.headers.get('X-Offline-Sync') == 'true':
                return jsonify({"status": "error", "message": "Invalid numeric input"}), 400
            flash("Error: Invalid numeric input.", "danger")
            return redirect("/inventory")

        if added_stock <= 0:
            if request.is_json or request.headers.get('X-Offline-Sync') == 'true':
                return jsonify({"status": "error", "message": "Restock quantity must be greater than zero."}), 400
            flash("Error: Restock quantity must be greater than zero.", "danger")
            return redirect("/inventory")

        min_sp = purchase_price + purchase_price * 0.5

        if selling_price < min_sp:
            if request.is_json or request.headers.get('X-Offline-Sync') == 'true':
                return jsonify({"status": "error", "message": f"Selling price cannot be less than Minimum S.P ({min_sp})"}), 400
            flash(f"Error: Selling price cannot be less than Minimum S.P (KSh {min_sp:,.2f}).", "danger")
            return redirect("/inventory")

        existing_product = Product.query.filter(Product.name.ilike(raw_name)).first()

        if existing_product:
            existing_product.stock += added_stock
            existing_product.purchase_price = purchase_price
            existing_product.selling_price = selling_price
            existing_product.min_selling_price = min_sp
            
            db.session.commit()
            if request.is_json or request.headers.get('X-Offline-Sync') == 'true':
                return jsonify({"status": "success", "message": "Stock added successfully!"}), 200
            flash(f"Restocked! Added {added_stock} units to '{existing_product.name}'.", "success")
        else:
            formatted_name = raw_name.title()
            try:
                new_product = Product(
                    name=formatted_name,
                    purchase_price=purchase_price,
                    min_selling_price=min_sp,
                    selling_price=selling_price,
                    stock=added_stock
                )
                db.session.add(new_product)
                db.session.commit()
                if request.is_json or request.headers.get('X-Offline-Sync') == 'true':
                    return jsonify({"status": "success", "message": "Product added successfully!"}), 200
                flash(f"New product '{formatted_name}' added to inventory!", "success")
            except IntegrityError:
                db.session.rollback()
                if request.is_json or request.headers.get('X-Offline-Sync') == 'true':
                    return jsonify({"status": "error", "message": "Product creation conflict."}), 400
                flash("Error: Product creation conflict.", "danger")

        return redirect("/inventory")

    search_query = request.args.get("search", "").strip()
    max_stock = request.args.get("max_stock", "")
    
    query = Product.query
    
    if search_query:
        sq_no_space = search_query.replace(" ", "")
        query = query.filter(func.replace(Product.name, ' ', '').ilike(f"%{sq_no_space}%"))
        
    if max_stock.isdigit():
        query = query.filter(Product.stock <= int(max_stock))

    products = query.all()
    all_products = Product.query.all()
    total_valuation = sum(p.purchase_price * p.stock for p in all_products)

    return render_template("inventory.html", products=products, all_products=all_products, search_query=search_query, max_stock=max_stock, total_valuation=total_valuation)

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
        product.stock = int(data.get("stock", product.stock))
    except ValueError:
        pass
        
    product.min_selling_price = product.purchase_price + (product.purchase_price * 0.5)
    
    try:
        db.session.commit()
        if request.is_json or request.headers.get('X-Offline-Sync') == 'true':
            return jsonify({"status": "success", "message": "Product updated securely."}), 200
        flash("Product updated securely.", "success")
    except IntegrityError:
        db.session.rollback()
        if request.is_json or request.headers.get('X-Offline-Sync') == 'true':
            return jsonify({"status": "error", "message": "Ensure product name is unique."}), 400
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
        if request.is_json or request.headers.get('X-Offline-Sync') == 'true':
            return jsonify({"status": "success", "message": "Product deleted."}), 200
        flash("Product deleted securely.", "success")
    except IntegrityError:
        db.session.rollback()
        if request.is_json or request.headers.get('X-Offline-Sync') == 'true':
            return jsonify({"status": "error", "message": "Cannot delete product linked to past logs."}), 400
        flash("Cannot delete product because it exists in past transaction logs. Consider editing stock to 0 instead.", "danger")
    return redirect("/inventory")

@app.route("/inventory/import", methods=["POST"])
@login_required
@admin_required
def import_inventory():
    if 'file' not in request.files:
        flash("No file part in the request.", "danger")
        return redirect("/inventory")
        
    file = request.files['file']
    
    if file.filename == '':
        flash("No file selected.", "danger")
        return redirect("/inventory")

    if file and file.filename.endswith('.csv'):
        try:
            # decode with 'utf-8-sig' to automatically remove Excel hidden BOM characters
            raw_text = file.stream.read().decode("utf-8-sig")
            
            # Detect whether Excel used comma (,) or semicolon (;)
            first_line = raw_text.splitlines()[0] if raw_text else ""
            delimiter = ';' if ';' in first_line and ',' not in first_line else ','
            
            stream = io.StringIO(raw_text, newline=None)
            csv_input = csv.DictReader(stream, delimiter=delimiter)
            
            imported_count = 0
            updated_count = 0
            
            for row in csv_input:
                # Clean up dictionary keys (lowercase, strip whitespace and underscores)
                clean_row = {str(k).strip().lower().replace('_', ' '): str(v).strip() for k, v in row.items() if k}
                
                # Flexible product name detection
                name = (
                    clean_row.get('product name') or 
                    clean_row.get('product') or 
                    clean_row.get('name') or 
                    clean_row.get('item name') or 
                    clean_row.get('item') or ''
                ).strip().title()
                
                if not name:
                    continue

                try:
                    # Flexible price & stock key detection
                    p_price = clean_row.get('purchase price') or clean_row.get('buy price') or clean_row.get('cost') or 0
                    s_price = clean_row.get('selling price') or clean_row.get('sell price') or clean_row.get('price') or 0
                    stk = clean_row.get('stock') or clean_row.get('current stock') or clean_row.get('qty') or clean_row.get('quantity') or 0

                    purchase_price = float(p_price)
                    selling_price = float(s_price)
                    stock = int(float(stk))
                except (ValueError, TypeError):
                    continue
                    
                min_sp = purchase_price + (purchase_price * 0.5)

                # Check if product exists in database
                existing_product = Product.query.filter(Product.name.ilike(name)).first()
                
                if existing_product:
                    existing_product.stock += stock
                    existing_product.purchase_price = purchase_price
                    existing_product.selling_price = selling_price
                    existing_product.min_selling_price = min_sp
                    updated_count += 1
                else:
                    new_product = Product(
                        name=name,
                        purchase_price=purchase_price,
                        min_selling_price=min_sp,
                        selling_price=selling_price,
                        stock=stock
                    )
                    db.session.add(new_product)
                    imported_count += 1
            
            db.session.commit()
            
            if imported_count == 0 and updated_count == 0:
                flash(f"Import finished, but 0 items matched. Headers found in CSV: {list(first_line.split(delimiter))}", "warning")
            else:
                flash(f"Import successful! Added {imported_count} new products and updated {updated_count} existing products.", "success")
            
        except Exception as e:
            db.session.rollback()
            flash(f"Error processing CSV: {str(e)}", "danger")
    else:
        flash("Unsupported file type. Please upload a .csv file.", "danger")

    return redirect("/inventory")


@app.route("/return_item", methods=["POST"])
@login_required
@admin_required
def return_item():
    data = request.get_json() if request.is_json else request.form
    
    sale_id = data.get("sale_id")
    product_id = data.get("product_id")
    
    try:
        return_qty = int(data.get("return_qty", 0))
    except ValueError:
        return_qty = 0

    if return_qty <= 0:
        if request.is_json or request.headers.get('X-Offline-Sync') == 'true':
            return jsonify({"status": "error", "message": "Return quantity must be greater than zero."}), 400
        flash("Error: Return quantity must be greater than zero.", "danger")
        return redirect(request.referrer or "/reports")

    sale_item = SaleItem.query.filter_by(sale_id=sale_id, product_id=product_id).first()
    
    if not sale_item:
        if request.is_json or request.headers.get('X-Offline-Sync') == 'true':
            return jsonify({"status": "error", "message": "Item not found in this transaction."}), 400
        flash("Error: Item not found in this transaction.", "danger")
        return redirect(request.referrer or "/reports")

    returned_already = db.session.query(func.sum(ReturnItem.quantity)).filter_by(sale_id=sale_id, product_id=product_id).scalar() or 0
    available_to_return = sale_item.quantity - returned_already

    if return_qty > available_to_return:
        if request.is_json or request.headers.get('X-Offline-Sync') == 'true':
            return jsonify({"status": "error", "message": f"Cannot return {return_qty} units. Only {available_to_return} left."}), 400
        flash(f"Error: Cannot return {return_qty} units. Only {available_to_return} unreturned units remain.", "danger")
        return redirect(request.referrer or "/reports")
        
    ret = ReturnItem(sale_id=sale_id, product_id=product_id, quantity=return_qty, date_returned=datetime.now())
    db.session.add(ret)

    product = Product.query.get(product_id)
    product.stock += return_qty

    refund_amount = return_qty * sale_item.price
    sale = Sale.query.get(sale_id)
    sale.total_amount -= refund_amount

    if sale.sale_type == "Debt" and sale.customer_id:
        active_debt = Debt.query.filter_by(customer_id=sale.customer_id, status="Active").first()
        if active_debt:
            active_debt.amount -= refund_amount
            active_debt.balance -= refund_amount
            if active_debt.balance <= 0:
                active_debt.balance = 0
                active_debt.status = "Cancelled"

    db.session.commit()
    
    if request.is_json or request.headers.get('X-Offline-Sync') == 'true':
        return jsonify({"status": "success", "message": "Return processed successfully!"}), 200
        
    flash(f"Successfully returned {return_qty}x {product.name}.", "success")
    return redirect(request.referrer or "/reports")

# 5. SALES (Staff + Admin)
@app.route("/sales", methods=["GET", "POST"])
@login_required
def sales():
    if request.method == "POST":
        customer_id = request.form.get("customer_id")
        new_customer_name = request.form.get("new_customer_name")
        product_id = request.form.get("product_id")
        sale_type = request.form.get("sale_type")
        
        try:
            quantity = int(request.form.get("quantity", 0))
        except ValueError:
            quantity = 0

        if quantity <= 0:
            flash("Error: Sale quantity must be greater than zero.", "danger")
            return redirect("/sales")

        product = Product.query.get(product_id)
        
        if quantity > product.stock:
            flash(f"Error: Only {product.stock} left in stock for {product.name}.", "danger")
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

        total_price = product.selling_price * quantity

        new_sale = Sale(customer_id=customer_id, total_amount=total_price, sale_type=sale_type, date=datetime.now())
        db.session.add(new_sale)
        db.session.flush()

        sale_item = SaleItem(sale_id=new_sale.id, product_id=product_id, quantity=quantity, price=product.selling_price)
        db.session.add(sale_item)

        product.stock -= quantity

        if sale_type == "Debt":
            active_debt = Debt.query.filter_by(customer_id=customer_id, status="Active").first()
            customer_obj = Customer.query.get(customer_id)
            cust_name = customer_obj.name if customer_obj else "Unknown"

            if active_debt:
                active_debt.amount += total_price
                active_debt.balance += total_price
            else:
                new_debt = Debt(
                    customer_id=customer_id, 
                    customer_name=cust_name,
                    amount=total_price, 
                    balance=total_price, 
                    date_taken=datetime.now()
                )
                db.session.add(new_debt)

        db.session.commit()
        flash(f"Sale recorded successfully as {sale_type}!", "success")
        return redirect("/sales")

    customers = Customer.query.all()
    products = Product.query.filter(Product.stock > 0).all()
    sales = Sale.query.order_by(Sale.date.desc()).limit(50).all()
    
    return render_template("sales.html", customers=customers, products=products, sales=sales, ReturnItem=ReturnItem, func=func, db=db)

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
            
    db.session.commit()
    flash(f"Sale #{sale.id} fully cancelled.", "success")
    return redirect(request.referrer or "/sales")

# 6. QUOTATIONS (Admin Only)
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
            
        valid_until = datetime.now() + timedelta(days=valid_days)
        new_quote = Quotation(customer_id=customer_id, total_amount=amount, date=datetime.now(), valid_until=valid_until)
        db.session.add(new_quote)
        db.session.commit()
        flash("Quotation saved successfully!", "success")
        return redirect("/quotations")

    customers = Customer.query.all()
    quotations = Quotation.query.all()
    return render_template("quotations.html", customers=customers, quotations=quotations)

# 7. REPORTS (Admin Only)
@app.route("/reports", methods=["GET", "POST"])
@login_required
@admin_required
def reports():
    end_date_dt = datetime.now()
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

    return render_template(
        "reports.html",
        outstanding_debts=outstanding_debts,
        total_payments=total_payments,
        total_sales=total_sales,
        profit=profit,
        customers=customers,
        start_date=start_date_dt.strftime("%Y-%m-%d"),
        end_date=end_date_dt.strftime("%Y-%m-%d"),
        sales_list=query_sales.all(),
        ReturnItem=ReturnItem, 
        func=func, 
        db=db
    )

@app.route("/export/inventory")
@login_required
@admin_required
def export_inventory():
    products = Product.query.all()
    
    def generate():
        yield 'ID,Product Name,Purchase Price,Minimum S.P,Selling Price,Stock\n'
        for p in products:
            yield f'{p.id},{p.name},{p.purchase_price},{p.min_selling_price},{p.selling_price},{p.stock}\n'
            
    return Response(generate(), mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename=inventory_report.csv'})

# 8. SERVICE BOOKINGS (Admin Only)
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

        if charge < 0:
            flash("Error: Charge cannot be negative.", "danger")
            return redirect("/bookings")

        booking_date = datetime.strptime(booking_date_str, "%Y-%m-%dT%H:%M")

        if booking_date.date() < datetime.today().date():
            flash("Error: Service booking date cannot be in the past.", "danger")
            return redirect("/bookings")

        new_booking = ServiceBooking(
            customer_id=customer_id,
            service_name=service_name,
            description=description,
            booking_date=booking_date,
            charge=charge
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


# 9. TOOL LOANS (Staff + Admin)
@app.route("/loans", methods=["GET", "POST"])
@login_required
def loans():
    if request.method == "POST":
        customer_id = request.form.get("customer_id")
        tool_name = request.form.get("tool_name").strip()
        return_date_str = request.form.get("return_date")

        return_date = datetime.strptime(return_date_str, "%Y-%m-%d")

        new_loan = ToolLoan(
            customer_id=customer_id,
            tool_name=tool_name,
            return_date=return_date
        )
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
    # This serves sw.js from the static folder, but makes the browser think it's at the root (/)
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
# Database Setup & Seeding (Runs for Gunicorn & Local)
# -------------------
with app.app_context():
    db.create_all()
    
    # Ensure Admin exists and password stays updated
    admin_user = User.query.filter_by(username="admin").first()
    if not admin_user:
        admin_user = User(username="admin", role="Admin")
        db.session.add(admin_user)
    admin_user.set_password(os.environ.get("ADMIN_PASSWORD", "pass364"))

    # Ensure Staff exists and password stays updated
    staff_user = User.query.filter_by(username="staff").first()
    if not staff_user:
        staff_user = User(username="staff", role="Staff")
        db.session.add(staff_user)
    staff_user.set_password(os.environ.get("STAFF_PASSWORD", "staff123"))
        
    db.session.commit()

# -------------------
# Run App (Local only)
# -------------------
if __name__ == "__main__":
    app.run(debug=True)