from flask import Flask, render_template, request, redirect
from flask_sqlalchemy import SQLAlchemy
import os

from models import db, Customer, Debt, Payment, Product, Sale, SaleItem

app = Flask(__name__)

# -------------------
# Configuration
# -------------------
app.config['SECRET_KEY'] = 'timos_secret_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///timos_erp.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)


# -------------------
# Routes
# -------------------

@app.route("/")
def home():
    return render_template("home.html")


# Customers
@app.route("/customers", methods=["GET", "POST"])
def customers():
    if request.method == "POST":
        name = request.form.get("name")
        contact_info = request.form.get("contact_info")

        if not name:  # basic validation
            return "Name is required", 400

        new_customer = Customer(name=name, contact_info=contact_info)
        db.session.add(new_customer)
        db.session.commit()

        return redirect("/customers")

    all_customers = Customer.query.all()
    return render_template("customers.html", customers=all_customers)
 

# Debts
@app.route("/debts", methods=["GET", "POST"])
def debts():
    if request.method == "POST":
        customer_id = request.form["customer_id"]
        amount = float(request.form["amount"])
        due_date = request.form["due_date"]

        new_debt = Debt(
            customer_id=customer_id,
            amount=amount,
            balance=amount,
            due_date=due_date,
            status="Active"
        )
        db.session.add(new_debt)
        db.session.commit()   # ✅ commit to DB

        return redirect("/debts")

    customers = Customer.query.all()
    all_debts = Debt.query.all()
    return render_template("debts.html", customers=customers, debts=all_debts)


# Payments
@app.route("/payments", methods=["GET", "POST"])
def payments():
    if request.method == "POST":
        debt_id = request.form["debt_id"]
        amount = float(request.form["amount"])
        date = request.form["date"]

        new_payment = Payment(debt_id=debt_id, amount=amount, date=date)
        db.session.add(new_payment)

        # Update debt balance
        debt = Debt.query.get(debt_id)
        debt.balance -= amount
        if debt.balance <= 0:
            debt.status = "Cleared"

        db.session.commit()
        return redirect("/payments")

    debts = Debt.query.all()
    payments = Payment.query.all()
    return render_template("payments.html", debts=debts, payments=payments)

# Inventory
@app.route("/inventory", methods=["GET", "POST"])
def inventory():
    if request.method == "POST":
        name = request.form["name"]
        purchase_price = float(request.form["purchase_price"])
        selling_price = float(request.form["selling_price"])
        stock = int(request.form["stock"])

        new_product = Product(
            name=name,
            purchase_price=purchase_price,
            selling_price=selling_price,
            stock=stock
        )
        db.session.add(new_product)
        db.session.commit()

        return redirect("/inventory")

    products = Product.query.all()
    return render_template("inventory.html", products=products)

# Sales
@app.route("/sales", methods=["GET", "POST"])
def sales():
    if request.method == "POST":
        customer_id = request.form["customer_id"]
        product_id = request.form["product_id"]
        quantity = int(request.form["quantity"])

        product = Product.query.get(product_id)
        total_price = product.selling_price * quantity

        # Create Sale
        new_sale = Sale(customer_id=customer_id, total_amount=total_price)
        db.session.add(new_sale)
        db.session.commit()

        # Create SaleItem
        sale_item = SaleItem(sale_id=new_sale.id, product_id=product_id, quantity=quantity)
        db.session.add(sale_item)

        # Reduce stock
        product.stock -= quantity

        db.session.commit()
        return redirect("/sales")

    customers = Customer.query.all()
    products = Product.query.all()
    sales = Sale.query.all()
    return render_template("sales.html", customers=customers, products=products, sales=sales)
# Reports with filters
@app.route("/reports", methods=["GET", "POST"])
def reports():
    # Default filters
    customer_id = None
    start_date = None
    end_date = None

    if request.method == "POST":
        customer_id = request.form.get("customer_id")
        start_date = request.form.get("start_date")
        end_date = request.form.get("end_date")

    # Outstanding debts (filtered by customer if chosen)
    query_debts = Debt.query.filter(Debt.status == "Active")
    if customer_id:
        query_debts = query_debts.filter(Debt.customer_id == customer_id)
    outstanding_debts = query_debts.all()

    # Payments
    query_payments = Payment.query
    if customer_id:
        query_payments = query_payments.join(Debt).filter(Debt.customer_id == customer_id)
    if start_date and end_date:
        query_payments = query_payments.filter(Payment.date.between(start_date, end_date))
    total_payments = sum(p.amount for p in query_payments.all())

    # Sales
    query_sales = Sale.query
    if customer_id:
        query_sales = query_sales.filter(Sale.customer_id == customer_id)
    if start_date and end_date:
        query_sales = query_sales.filter(Sale.date.between(start_date, end_date))
    total_sales = sum(s.total_amount for s in query_sales.all())

    # Profit
    profit = 0
    for item in SaleItem.query.all():
        product = Product.query.get(item.product_id)
        profit += (product.selling_price - product.purchase_price) * item.quantity

    customers = Customer.query.all()

    return render_template(
        "reports.html",
        outstanding_debts=outstanding_debts,
        total_payments=total_payments,
        total_sales=total_sales,
        profit=profit,
        customers=customers
    )


# -------------------
# Run App + Create DB
# -------------------
if __name__ == "__main__":
    with app.app_context():
        db.create_all()   # ✅ ensures tables exist
    app.run(debug=True)
