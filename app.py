import re
import csv
from flask import Response
from flask import Flask, render_template, request, redirect, flash, url_for
from datetime import datetime, timedelta
from sqlalchemy.exc import IntegrityError
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from models import db, User, Customer, Debt, Payment, Product, Sale, SaleItem, ReturnItem, Quotation, ServiceBooking, ToolLoan

app = Flask(__name__)

# -------------------
# Configuration
# -------------------
app.config['SECRET_KEY'] = 'timos_secret_key_change_in_production'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///timos_erp_v2.db' 
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

# -------------------
# Flask-Login Setup
# -------------------
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message_category = 'danger'

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

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

# 1. CUSTOMERS
@app.route("/customers", methods=["GET", "POST"])
@login_required
def customers():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        contact_info = request.form.get("contact_info", "").strip()

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

    all_customers = Customer.query.all()
    return render_template("customers.html", customers=all_customers)

@app.route("/customers/edit/<int:pid>", methods=["POST"])
@login_required
def edit_customer(pid):
    customer = Customer.query.get_or_404(pid)
    name = request.form.get("name", "").strip()
    contact_info = request.form.get("contact_info", "").strip()

    if not re.match(r"^[A-Za-z\s]+$", name):
        flash("Error: Customer name must contain only letters and spaces.", "danger")
        return redirect("/customers")
        
    if len(contact_info) < 10:
        flash("Error: Contact info must be at least 10 characters/numbers long.", "danger")
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
        customer_name = request.form.get("customer_name").strip()
        product_name = request.form.get("product_name", "").strip() 
        amount = float(request.form.get("amount"))

        new_debt = Debt(
            customer_id=customer_id, 
            customer_name=customer_name, 
            product_name=product_name, 
            amount=amount, 
            balance=amount,
            date=datetime.utcnow()
        )
        db.session.add(new_debt)
        db.session.commit()
        flash("Debt recorded successfully!", "success")
        return redirect("/debts")
    
    all_debts = Debt.query.order_by(Debt.date.desc()).all()
    customers = Customer.query.all()
    return render_template("debts.html", debts=all_debts, customers=customers)

# 3. PAYMENTS
@app.route("/payments", methods=["GET", "POST"])
@login_required
def payments():
    if request.method == "POST":
        debt_id = request.form.get("debt_id")
        amount = float(request.form.get("amount"))

        new_payment = Payment(debt_id=debt_id, amount=amount, date=datetime.now())
        db.session.add(new_payment)

        debt = Debt.query.get(debt_id)
        debt.balance -= amount
        if debt.balance <= 0:
            debt.status = "Cleared"

        db.session.commit()
        flash("Payment recorded successfully!", "success")
        return redirect("/payments")

    debts = Debt.query.filter_by(status="Active").all()
    payments = Payment.query.order_by(Payment.date.desc()).all()
    return render_template("payments.html", debts=debts, payments=payments)

# 4. INVENTORY
@app.route("/inventory", methods=["GET", "POST"])
@login_required
def inventory():
    if request.method == "POST":
        raw_name = request.form.get("name", "").strip()
        purchase_price = int(request.form.get("purchase_price"))
        selling_price = float(request.form.get("selling_price"))
        added_stock = int(request.form.get("stock"))

        min_sp = purchase_price + purchase_price * 0.5

        if selling_price < min_sp:
            flash(f"Error: Selling price cannot be less than Minimum S.P (KSh {min_sp:,.2f}).", "danger")
            return redirect("/inventory")

        existing_product = Product.query.filter(Product.name.ilike(raw_name)).first()

        if existing_product:
            existing_product.stock += added_stock
            existing_product.purchase_price = purchase_price
            existing_product.selling_price = selling_price
            existing_product.min_selling_price = min_sp
            
            db.session.commit()
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
                flash(f"New product '{formatted_name}' added to inventory!", "success")
            except IntegrityError:
                db.session.rollback()
                flash("Error: Product creation conflict.", "danger")

        return redirect("/inventory")

    search_query = request.args.get("search", "").strip()
    all_products = Product.query.all()
    
    if search_query:
        products = Product.query.filter(Product.name.ilike(f"%{search_query}%")).all()
    else:
        products = all_products

    total_valuation = sum(p.purchase_price * p.stock for p in all_products)

    return render_template("inventory.html", products=products, all_products=all_products, search_query=search_query, total_valuation=total_valuation)

@app.route("/return_item", methods=["POST"])
@login_required
def return_item():
    sale_id = request.form.get("sale_id")
    product_id = request.form.get("product_id")
    return_qty = int(request.form.get("return_qty"))

    sale_item = SaleItem.query.filter_by(sale_id=sale_id, product_id=product_id).first()
    
    if sale_item and return_qty <= sale_item.quantity:
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
                active_debt.balance -= refund_amount
                if active_debt.balance <= 0:
                    active_debt.status = "Cleared"

        db.session.commit()
        flash(f"Successfully returned {return_qty}x {product.name}. Stock and balances updated.", "success")
    else:
        flash("Error processing return. Invalid quantity.", "danger")
        
    return redirect("/reports")

# 5. SALES
@app.route("/sales", methods=["GET", "POST"])
@login_required
def sales():
    if request.method == "POST":
        customer_id = request.form.get("customer_id")
        new_customer_name = request.form.get("new_customer_name")
        product_id = request.form.get("product_id")
        quantity = int(request.form.get("quantity"))
        sale_type = request.form.get("sale_type")

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
                    product_name=product.name,
                    amount=total_price, 
                    balance=total_price, 
                    date=datetime.utcnow()
                )
                db.session.add(new_debt)

        db.session.commit()
        flash(f"Sale recorded successfully as {sale_type}!", "success")
        return redirect("/sales")

    customers = Customer.query.all()
    products = Product.query.all()
    sales = Sale.query.order_by(Sale.date.desc()).all()
    return render_template("sales.html", customers=customers, products=products, sales=sales)

# 6. QUOTATIONS
@app.route("/quotations", methods=["GET", "POST"])
@login_required
def quotations():
    if request.method == "POST":
        customer_id = request.form.get("customer_id")
        amount = float(request.form.get("amount"))
        valid_days = int(request.form.get("valid_days", 7))
        
        valid_until = datetime.now() + timedelta(days=valid_days)
        new_quote = Quotation(customer_id=customer_id, total_amount=amount, date=datetime.now(), valid_until=valid_until)
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
                profit += (item.price - item.product.purchase_price) * item.quantity

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
        sales_list=query_sales.all()
    )

@app.route("/sales/cancel/<int:sale_id>", methods=["POST"])
@login_required
def cancel_sale(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    
    if sale.status == "Cancelled":
        flash("This sale has already been cancelled.", "warning")
        return redirect("/sales")

    sale.status = "Cancelled"
    
    for item in sale.items:
        product = Product.query.get(item.product_id)
        if product:
            product.stock += item.quantity
            
    db.session.commit()
    flash(f"Sale #{sale.id} cancelled. Stock restored and profit recalculated.", "success")
    return redirect("/sales")

@app.route("/export/inventory")
@login_required
def export_inventory():
    products = Product.query.all()
    
    def generate():
        yield 'ID,Product Name,Purchase Price,Minimum S.P,Selling Price,Stock\n'
        for p in products:
            yield f'{p.id},{p.name},{p.purchase_price},{p.min_selling_price},{p.selling_price},{p.stock}\n'
            
    return Response(generate(), mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename=inventory_report.csv'})

# 8. SERVICE BOOKINGS
@app.route("/bookings", methods=["GET", "POST"])
@login_required
def bookings():
    if request.method == "POST":
        customer_id = request.form.get("customer_id")
        service_name = request.form.get("service_name").strip()
        description = request.form.get("description", "").strip()
        booking_date_str = request.form.get("booking_date")

        booking_date = datetime.strptime(booking_date_str, "%Y-%m-%dT%H:%M")

        new_booking = ServiceBooking(
            customer_id=customer_id,
            service_name=service_name,
            description=description,
            booking_date=booking_date
        )
        db.session.add(new_booking)
        db.session.commit()
        flash("Service booking scheduled successfully!", "success")
        return redirect("/bookings")

    customers = Customer.query.all()
    all_bookings = ServiceBooking.query.order_by(ServiceBooking.booking_date.desc()).all()
    return render_template("bookings.html", customers=customers, bookings=all_bookings)

# 9. TOOL LOANS
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

@app.route("/loans/return/<int:loan_id>", methods=["POST"])
@login_required
def return_tool(loan_id):
    loan = ToolLoan.query.get_or_404(loan_id)
    loan.status = "Returned"
    db.session.commit()
    flash(f"Tool '{loan.tool_name}' marked as returned.", "success")
    return redirect("/loans")

# -------------------
# Run App & Seed Admin User
# -------------------
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        
        if not User.query.first():
            admin_user = User(username="admin", role="Admin")
            admin_user.set_password("admin123")
            db.session.add(admin_user)
            db.session.commit()
            print("Default admin created (username: admin, password: admin123)")

    app.run(debug=True)