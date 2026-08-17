from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# -------------------
# Customer Management
# -------------------
class Customer(db.Model):
    __tablename__ = "customer"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    contact_info = db.Column(db.String(200))

    # Relationships
    debts = db.relationship("Debt", back_populates="customer", lazy=True)
    sales = db.relationship("Sale", back_populates="customer", lazy=True)


# -------------------
# Debt Tracking
# -------------------
class Debt(db.Model):
    __tablename__ = "debt"
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    balance = db.Column(db.Float, nullable=False)
    due_date = db.Column(db.String(20))
    status = db.Column(db.String(20), default="Active")

    # Relationships
    customer = db.relationship("Customer", back_populates="debts")
    payments = db.relationship("Payment", back_populates="debt", lazy=True)


class Payment(db.Model):
    __tablename__ = "payment"
    id = db.Column(db.Integer, primary_key=True)
    debt_id = db.Column(db.Integer, db.ForeignKey("debt.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    date = db.Column(db.String(20))

    # Relationships
    debt = db.relationship("Debt", back_populates="payments")


# -------------------
# Inventory & Products
# -------------------
class Product(db.Model):
    __tablename__ = "product"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    purchase_price = db.Column(db.Float, default=0.0)
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
    customer_id = db.Column(db.Integer, db.ForeignKey("customer.id"), nullable=False)
    date = db.Column(db.String(20))
    total_amount = db.Column(db.Float)

    # Relationships
    customer = db.relationship("Customer", back_populates="sales")
    items = db.relationship("SaleItem", back_populates="sale", lazy=True)


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
