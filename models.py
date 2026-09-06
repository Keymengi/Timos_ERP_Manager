from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, timezone, timedelta
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

# -------------------
# Timezone Helper (EAT / UTC+3)
# -------------------
def get_eat_time():
    """Returns the current East Africa Time (UTC+3) reading, as a plain
    timestamp with no timezone tag attached.

    Deliberately NOT using a timezone-aware datetime here: when a
    timezone-aware value is saved to the live Postgres database, Postgres
    converts it back to plain UTC before storing it — silently undoing the
    +3 hours we just added. Returning a plain (untagged) reading avoids
    that conversion, so the East Africa time we calculate is exactly the
    time that gets saved and displayed.
    """
    return datetime.utcnow() + timedelta(hours=3)

# -------------------
# User Management
# -------------------
class User(db.Model, UserMixin):
    __tablename__ = "user"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(20), default="Staff") # 'Admin' or 'Staff'

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

# -------------------
# Customer Management
# -------------------
class Customer(db.Model):
    __tablename__ = "customer"
    pid = db.Column(db.Integer, primary_key=True) 
    name = db.Column(db.String(100), unique=True, nullable=False) 
    contact_info = db.Column(db.String(200))

    debts = db.relationship("Debt", back_populates="customer", lazy=True, cascade="all, delete-orphan")
    sales = db.relationship("Sale", back_populates="customer", lazy=True, cascade="all, delete-orphan")
    quotations = db.relationship("Quotation", back_populates="customer", lazy=True, cascade="all, delete-orphan")

# -------------------
# Debt Tracking
# -------------------
class Debt(db.Model):
    __tablename__ = "debt"
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.pid'), nullable=False)
    customer_name = db.Column(db.String(120), nullable=False)
    product_name = db.Column(db.String(255), nullable=True) 
    amount = db.Column(db.Float, nullable=False) 
    balance = db.Column(db.Float, nullable=False, default=0.0)
    status = db.Column(db.String(20), default="Active")
    date_taken = db.Column(db.DateTime, default=get_eat_time) 
    reminder_sent = db.Column(db.Boolean, default=False) # For the 24h background reminder
    
    customer = db.relationship("Customer", back_populates="debts")
    payments = db.relationship("Payment", back_populates="debt", lazy=True, cascade="all, delete-orphan")

class Payment(db.Model):
    __tablename__ = "payment"
    id = db.Column(db.Integer, primary_key=True)
    debt_id = db.Column(db.Integer, db.ForeignKey("debt.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.DateTime, default=get_eat_time)

    debt = db.relationship("Debt", back_populates="payments")

# -------------------
# Inventory & Products
# -------------------
class Product(db.Model):
    __tablename__ = "product"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    purchase_price = db.Column(db.Float, default=0.0)
    min_selling_price = db.Column(db.Float, default=0.0)
    selling_price = db.Column(db.Float, default=0.0)
    stock = db.Column(db.Float, default=0.0)

    sale_items = db.relationship("SaleItem", back_populates="product", lazy=True, cascade="all, delete-orphan")

# -------------------
# Sales & Transactions
# -------------------
class Sale(db.Model):
    __tablename__ = "sale"
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.pid"), nullable=True)
    date = db.Column(db.DateTime, default=get_eat_time)
    total_amount = db.Column(db.Float, nullable=False)
    sale_type = db.Column(db.String(20), default="Normal") 
    status = db.Column(db.String(20), default="Completed")

    customer = db.relationship("Customer", back_populates="sales")
    items = db.relationship("SaleItem", back_populates="sale", lazy=True, cascade="all, delete-orphan")
    returns = db.relationship("ReturnItem", back_populates="sale", lazy=True, cascade="all, delete-orphan")

class SaleItem(db.Model):
    __tablename__ = "sale_item"
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sale.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    price = db.Column(db.Float, nullable=False)

    sale = db.relationship("Sale", back_populates="items")
    product = db.relationship("Product", back_populates="sale_items")

# -------------------
# Returns
# -------------------
class ReturnItem(db.Model):
    __tablename__ = "return_item"
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey("sale.id"), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey("product.id"), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    date_returned = db.Column(db.DateTime, default=get_eat_time)

    sale = db.relationship("Sale", back_populates="returns")
    product = db.relationship("Product")

# -------------------
# Quotations
# -------------------
class Quotation(db.Model):
    __tablename__ = "quotation"
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.pid"), nullable=True)
    date = db.Column(db.DateTime, default=get_eat_time)
    valid_until = db.Column(db.DateTime)
    total_amount = db.Column(db.Float, default=0.0)

    customer = db.relationship("Customer", back_populates="quotations")

# -------------------
# Appointments & Service Bookings
# -------------------
class ServiceBooking(db.Model):
    __tablename__ = "service_booking"
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.pid"), nullable=False)
    service_name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=True)
    booking_date = db.Column(db.DateTime, nullable=False)
    charge = db.Column(db.Float, default=0.0) 
    status = db.Column(db.String(20), default="Pending")
    reminder_sent = db.Column(db.Boolean, default=False) # For the booking background reminder

    customer = db.relationship("Customer", backref="service_bookings", lazy=True)

# -------------------
# SMS Notification Log
# -------------------
class SMSLog(db.Model):
    __tablename__ = "sms_log"
    id = db.Column(db.Integer, primary_key=True)
    recipient_name = db.Column(db.String(120), nullable=True)
    phone_number = db.Column(db.String(20), nullable=True)
    message = db.Column(db.Text, nullable=False)
    category = db.Column(db.String(30), nullable=False)  # 'Debt Reminder' or 'Booking Reminder'
    status = db.Column(db.String(20), default="Sent")  # 'Sent', 'Failed', 'Console'
    date_sent = db.Column(db.DateTime, default=get_eat_time)

# -------------------
# Tool Loan Tracking
# -------------------
class ToolLoan(db.Model):
    __tablename__ = "tool_loan"
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.pid"), nullable=False)
    tool_name = db.Column(db.String(120), nullable=False)
    date_borrowed = db.Column(db.DateTime, default=get_eat_time)
    return_date = db.Column(db.DateTime, nullable=False)
    status = db.Column(db.String(20), default="Active")

    customer = db.relationship("Customer", backref="tool_loans", lazy=True)