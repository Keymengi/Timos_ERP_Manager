from flask import Flask, render_template, request, redirect, flash, url_for
from datetime import datetime, timedelta
from sqlalchemy.exc import IntegrityError
from models import db, User, Customer, Debt, Payment, Product, Sale, SaleItem, ReturnItem, Quotation

app = Flask(__name__)

# -------------------
# Configuration
# -------------------
app.config['SECRET_KEY'] = 'timos_secret_key'
# Changed to v2 so it auto-creates the new database without crashing
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///timos_erp_v2.db' 
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

# Feature: Add commas on the amount for easy reading
@app.template_filter('currency')
def currency_format(value):
    if value is None:
        return "0.00"
    return f"{value:,.2f}"

# -------------------
# Routes
# -------------------

@app.route("/")
def home():
    return render_template("home.html")

# 1. CUSTOMERS
@app.route("/customers", methods=["GET", "POST"])
def customers():
    if request.method == "POST":
        name = request.form.get("name")
        contact_info = request.form.get("contact_info")

        try:
            new_customer = Customer(name=name, contact_info=contact_info)
            db.session.add(new_customer)
            db.session.commit()
            flash("Customer added successfully!", "success")
        except IntegrityError:
            # Feature: Prevent adding a customer twice
            db.session.rollback()
            flash(f"Error: Customer name '{name}' already exists!", "danger")
            
        return redirect("/customers")

    all_customers = Customer.query.all()
    return render_template("customers.html", customers=all_customers)

# Feature: Enable editing of customer records
@app.route("/customers/edit/<int:pid>", methods=["POST"])
def edit_customer(pid):
    customer = Customer.query.get_or_404(pid)
    customer.name = request.form.get("name")
    customer.contact_info = request.form.get("contact_info")
    try:
        db.session.commit()
        flash("Customer updated successfully!", "success")
    except IntegrityError:
        db.session.rollback()
        flash("Error: That name is already taken by another customer.", "danger")
    return redirect("/customers")

# 2. DEBTS
@app.route("/debts", methods=["GET", "POST"])
def debts():
    if request.method == "POST":
        customer_id = request.form.get("customer_id")
        new_customer_name = request.form.get("new_customer_name")
        amount = float(request.form.get("amount"))

        # Feature: Add customer directly without going to customers table
        if new_customer_name:
            cust = Customer.query.filter_by(name=new_customer_name).first()
            if not cust:
                cust = Customer(name=new_customer_name)
                db.session.add(cust)
                db.session.flush() # Get the new PID
            customer_id = cust.pid

        if not customer_id:
            flash("You must select or create a customer.", "danger")
            return redirect("/debts")

        # Feature: Update the debt amount in the same record instead of adding a new one
        active_debt = Debt.query.filter_by(customer_id=customer_id, status="Active").first()
        
        if active_debt:
            active_debt.amount += amount
            active_debt.balance += amount
        else:
            # Feature: Auto-record the date of the taken debt via default=datetime.now
            new_debt = Debt(customer_id=customer_id, amount=amount, balance=amount, date_taken=datetime.now())
            db.session.add(new_debt)

        db.session.commit()
        flash("Debt processed successfully!", "success")
        return redirect("/debts")

    customers = Customer.query.all()
    all_debts = Debt.query.all()
    return render_template("debts.html", customers=customers, debts=all_debts)

# 3. PAYMENTS
@app.route("/payments", methods=["GET", "POST"])
def payments():
    if request.method == "POST":
        debt_id = request.form.get("debt_id")
        amount = float(request.form.get("amount"))
        # Feature: Date added automatically by the server

        new_payment = Payment(debt_id=debt_id, amount=amount, date=datetime.now())
        db.session.add(new_payment)

        debt = Debt.query.get(debt_id)
        debt.balance -= amount
        if debt.balance <= 0:
            debt.status = "Cleared"

        db.session.commit()
        flash("Payment recorded successfully!", "success")
        return redirect("/payments")

    # Only show active debts for payment
    debts = Debt.query.filter_by(status="Active").all()
    payments = Payment.query.order_by(Payment.date.desc()).all()
    return render_template("payments.html", debts=debts, payments=payments)

# 4. INVENTORY & RETURNS
@app.route("/inventory", methods=["GET", "POST"])
def inventory():
    if request.method == "POST":
        name = request.form.get("name")
        # Feature: Purchase price changes by 1 unit (converted to Integer)
        purchase_price = int(request.form.get("purchase_price"))
        selling_price = float(request.form.get("selling_price"))
        stock = int(request.form.get("stock"))

        new_product = Product(name=name, purchase_price=purchase_price, selling_price=selling_price, stock=stock)
        db.session.add(new_product)
        db.session.commit()
        flash("Product added to inventory!", "success")
        return redirect("/inventory")

    products = Product.query.all()
    return render_template("inventory.html", products=products)

# Feature: Allow return of sold item and deduct from sales or debts
@app.route("/return_item", methods=["POST"])
def return_item():
    sale_id = request.form.get("sale_id")
    product_id = request.form.get("product_id")
    return_qty = int(request.form.get("return_qty"))

    sale_item = SaleItem.query.filter_by(sale_id=sale_id, product_id=product_id).first()
    
    if sale_item and return_qty <= sale_item.quantity:
        # 1. Log the return
        ret = ReturnItem(sale_id=sale_id, product_id=product_id, quantity=return_qty, date_returned=datetime.now())
        db.session.add(ret)

        # 2. Add stock back to inventory
        product = Product.query.get(product_id)
        product.stock += return_qty

        # 3. Adjust financial totals
        refund_amount = return_qty * sale_item.price
        sale = Sale.query.get(sale_id)
        sale.total_amount -= refund_amount

        # 4. If it was a debt sale, deduct from their debt balance
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
def sales():
    if request.method == "POST":
        customer_id = request.form.get("customer_id")
        new_customer_name = request.form.get("new_customer_name")
        product_id = request.form.get("product_id")
        quantity = int(request.form.get("quantity"))
        sale_type = request.form.get("sale_type") # "Normal" or "Debt"

        product = Product.query.get(product_id)
        
        # Validation: Check stock
        if quantity > product.stock:
            flash(f"Error: Only {product.stock} left in stock for {product.name}.", "danger")
            return redirect("/sales")

        # Feature: Dynamic customer creation
        if new_customer_name:
            cust = Customer.query.filter_by(name=new_customer_name).first()
            if not cust:
                cust = Customer(name=new_customer_name)
                db.session.add(cust)
                db.session.flush()
            customer_id = cust.pid

        # Feature: Take a null input on customer name (Walk-in)
        if not customer_id:
            customer_id = None
            if sale_type == "Debt":
                flash("Error: You cannot record a Debt for an unknown walk-in customer.", "danger")
                return redirect("/sales")

        total_price = product.selling_price * quantity

        # Feature: Indicate normal sale or debt
        new_sale = Sale(customer_id=customer_id, total_amount=total_price, sale_type=sale_type, date=datetime.now())
        db.session.add(new_sale)
        db.session.flush()

        sale_item = SaleItem(sale_id=new_sale.id, product_id=product_id, quantity=quantity, price=product.selling_price)
        db.session.add(sale_item)

        # Feature: Automatically update stock quantity
        product.stock -= quantity

        # Feature: If sale type is Debt, auto-update the unified debt record
        if sale_type == "Debt":
            active_debt = Debt.query.filter_by(customer_id=customer_id, status="Active").first()
            if active_debt:
                active_debt.amount += total_price
                active_debt.balance += total_price
            else:
                new_debt = Debt(customer_id=customer_id, amount=total_price, balance=total_price, date_taken=datetime.now())
                db.session.add(new_debt)

        db.session.commit()
        flash(f"Sale recorded successfully as {sale_type}!", "success")
        return redirect("/sales")

    customers = Customer.query.all()
    products = Product.query.all()
    sales = Sale.query.order_by(Sale.date.desc()).all()
    return render_template("sales.html", customers=customers, products=products, sales=sales)

# 6. QUOTATIONS (New Module)
@app.route("/quotations", methods=["GET", "POST"])
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
def reports():
    # Feature: Hold data actively for a week by default
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
            # Set to end of the day to capture all times on that date
            end_date_dt = datetime.strptime(req_end, "%Y-%m-%d").replace(hour=23, minute=59, second=59)

    # Outstanding debts (Global, not bound by date)
    query_debts = Debt.query.filter(Debt.status == "Active")
    if customer_id:
        query_debts = query_debts.filter(Debt.customer_id == customer_id)
    outstanding_debts = query_debts.all()

    # Payments within timeframe
    query_payments = Payment.query.filter(Payment.date.between(start_date_dt, end_date_dt))
    if customer_id:
        query_payments = query_payments.join(Debt).filter(Debt.customer_id == customer_id)
    total_payments = sum(p.amount for p in query_payments.all())

    # Sales within timeframe
    query_sales = Sale.query.filter(Sale.date.between(start_date_dt, end_date_dt))
    if customer_id:
        query_sales = query_sales.filter(Sale.customer_id == customer_id)
    
    total_sales = 0
    profit = 0
    
    for sale in query_sales.all():
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
        # Pass formatted strings back to the template to prepopulate date inputs
        start_date=start_date_dt.strftime("%Y-%m-%d"),
        end_date=end_date_dt.strftime("%Y-%m-%d"),
        sales_list=query_sales.all() # Passing sales list so we can trigger Returns from Reports
    )

# -------------------
# Run App + Create DB
# -------------------
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    # Debug=True makes it restart automatically when you change the file
    app.run(debug=True)