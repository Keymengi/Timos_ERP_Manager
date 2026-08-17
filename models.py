from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

# -------------------
# User Management (New)
# -------------------
class User(db.Model):
    __tablename__ = "user"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default="Sales Agent") # e.g., Admin, Sales Agent

# -------------------
# Customer Management
# -------------------
class Customer(db.Model):
    __tablename__ = "customer"
    # Changed ID to PID per requirements
    pid = db.Column(db.Integer, primary_key=True) 
    # Added unique=True so a customer can't be added twice
    name = db.Column(db.String(100), unique=True, nullable=False) 
    contact_info = db.Column(db.String(200))

    # Relationships
    debts = db.relationship("Debt", back_populates="customer", lazy=True)
    sales = db.relationship("Sale", back_populates="customer", lazy=True)
    quotations = db.relationship("Quotation", back_populates="customer", lazy=True)

# -------------------
# Debt Tracking
# -------------------
class Debt(db.Model):
    __tablename__ = "debt"
    id = db.Column(db.Integer, primary_key=True)
    # customer_id must map to customer.pid now
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.pid"), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    balance = db.Column(db.Float, nullable=False)
    # New: Date the debt was taken
    date_taken = db.Column(db.DateTime, default=datetime.utcnow)
    # Using DateTime instead of String for accurate reporting
    due_date = db.Column(db.DateTime) 
    status = db.Column(db.String(20), default="Active")

    # Relationships
    customer = db.relationship("Customer", back_populates="debts")
    payments = db.relationship("Payment", back_populates="debt", lazy=True)

class Payment(db.Model):
    __tablename__ = "payment"
    id = db.Column(db.Integer, primary_key=True)
    debt_id = db.Column(db.Integer, db.ForeignKey("debt.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    debt = db.relationship("Debt", back_populates="payments")

# -------------------
# Inventory & Products
# -------------------
class Product(db.Model):
    __tablename__ = "product"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    # Purchase price changed to Integer to enforce whole numbers
    purchase_price = db.Column(db.Integer, default=0) 
    selling_price = db.Column(db.Float, default=0.0)
    stock = db.Column(db.Integer, default=0)

    # Relationships
    sale_items = db.relationship("SaleItem", back_populates="product", lazy=True)

# -------------------
# Sales & Transactions
# -------------------
class Sale(db.Model):
    __tablename__ = "sale"
    id = db.Column(db.Integer, primary_key=True)
    # Changed nullable=True to allow null inputs for walk-in customers
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.pid"), nullable=True)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    total_amount = db.Column(db.Float, nullable=False)
    # New: Indicate Normal or Debt
    sale_type = db.Column(db.String(20), default="Normal") 

    # Relationships
    customer = db.relationship("Customer", back_populates="sales")
    items = db.relationship("SaleItem", back_populates="sale", lazy=True)
    returns = db.relationship("ReturnItem", back_populates="sale", lazy=True)

class SaleItem(db.Model):
    __tablename__ = "sale_item"
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sale.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    price = db.Column(db.Float, nullable=False)

    # Relationships
    sale = db.relationship("Sale", back_populates="items")
    product = db.relationship("Product", back_populates="sale_items")

# -------------------
# Returns (New)
# -------------------
class ReturnItem(db.Model):
    __tablename__ = "return_item"
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sale.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    date_returned = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    sale = db.relationship("Sale", back_populates="returns")
    product = db.relationship("Product") # Links back to product to restock

# -------------------
# Quotations (New)
# -------------------
class Quotation(db.Model):
    __tablename__ = "quotation"
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.pid"), nullable=True)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    valid_until = db.Column(db.DateTime)
    total_amount = db.Column(db.Float, default=0.0)

    # Relationships
    customer = db.relationship("Customer", back_populates="quotations")