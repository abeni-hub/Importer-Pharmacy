from django.db import models
from django.conf import settings
from django.utils import timezone
from django.utils.timezone import now
from decimal import Decimal
from django.core.validators import MinValueValidator, MaxValueValidator
import uuid


class Department(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=10, unique=True)
    name = models.CharField(max_length=255)

    def __str__(self):
        return f"{self.code} - {self.name}"


class Medicine(models.Model):
    class UnitChoices(models.TextChoices):
        PK = "Pk", "Pk"
        BOTTLE = "Bottle", "Bottle"
        SACHET = "Sachet", "Sachet"
        AMPULE = "Ampule", "Ampule"
        VIAL = "Vial", "Vial"
        TIN = "Tin", "Tin"
        STRIP = "Strip", "Strip"
        TUBE = "Tube", "Tube"
        BOX = "Box", "Box"
        COSMETICS = "Cosmetics", "Cosmetics"
        TEN_X_100 = "10 x 100", "10 x 100"
        OF_10 = "Of 10", "Of 10"
        OF_20 = "Of 20", "Of 20"
        OF_14 = "Of 14", "Of 14"
        OF_28 = "Of 28", "Of 28"
        OF_30 = "Of 30", "Of 30"
        SUPPOSITORY = "Suppository", "Suppository"
        PCS = "Pcs", "Pcs"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    #code_no = models.CharField(max_length=50, unique=True)
    brand_name = models.CharField(max_length=255)
    item_name = models.CharField(max_length=255, blank=True, null=True)
    batch_no = models.CharField(max_length=100 , unique=True,db_index=True)
    manufacture_date = models.DateField(null=True, blank= True)
    expire_date = models.DateField(db_index=True)
    buying_price = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0)  # selling price
    #stock = models.IntegerField(default=0)
    stock_in_unit = models.IntegerField(default=0, help_text="Stock count per individual unit (e.g., bottle, strip, etc.)")
    #stock_in_carton = models.IntegerField(default=0, help_text="Number of cartons or boxes in stock")
    stock_carton = models.PositiveIntegerField(default=0)
    units_per_carton = models.PositiveIntegerField(default=1)
    stock_in_unit = models.PositiveIntegerField(default=0)
    low_stock_threshold = models.IntegerField(default=10)
    low_threshold = models.IntegerField(
        default=10,
        help_text="Threshold to trigger low stock alerts (in units)."
    )
    expired_date = models.DateField(null=True, blank=True,db_index=True)
    company_name = models.CharField(max_length=255, blank=True, null=True)
    FSNO = models.CharField(blank=True, null=True)
    department = models.ForeignKey(Department,on_delete=models.SET_NULL,null=True, blank=True,related_name='medicines')
    attachment = models.FileField(upload_to="medicine_attachments/", blank=True, null=True)

    # ✅ new enum field for unit
    unit = models.CharField(
        max_length=20,
        choices=UnitChoices.choices,
        default=UnitChoices.PCS,
        editable=True  # change to False if you don't want admins to edit in admin
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    def is_out_of_stock(self):
        total_units = (self.stock_carton * self.units_per_carton) + self.stock_in_unit
        return total_units <= 0

    def is_expired(self):
        return timezone.localdate() > self.expire_date

    def is_nearly_expired(self, days=30):
        delta = (self.expire_date - timezone.localdate()).days
        return 0 <= delta <= days
    def profit_per_item(self):
        return float(self.price) - float(self.buying_price)

    def total_profit(self):
       total_units = (self.stock_carton * self.units_per_carton) + self.stock_in_unit
       return self.profit_per_item() * total_units

    @property
    def total_stock_units(self):
        return (self.stock_carton * self.units_per_carton) + self.stock_in_unit

    def __str__(self):
        dept = self.department.name if self.department else "No Dept"
        return f"{self.brand_name} ({self.item_name}) - {dept}"



PAYMENT_METHOD_CHOICES = [
    ('cash', 'Cash'),
    ('transfer', 'Bank Transfer'),
]

class Sale(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    voucher_number = models.CharField(max_length=20, unique=True, editable=False,db_index=True)
    sold_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    customer_name = models.CharField(max_length=255, blank=True, null=True)
    customer_phone = models.CharField(max_length=20, blank=True, null=True)
    sale_date = models.DateTimeField(auto_now_add=True)
    TIN_number = models.CharField(max_length=20, blank=True, null=True)
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, default='cash')

    discount_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0
    )
    base_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    discounted_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    discounted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="discounted_sales",
    )

    def __str__(self):
        return f"Sale {self.id} by {self.sold_by or 'Unknown'} on {self.sale_date}"
    def __str__(self):
        return f"Sale {self.voucher_number or self.id} by {self.sold_by or 'Unknown'} on {self.sale_date}"

    # --------------------------
    # AUTO VOUCHER NUMBER LOGIC
    # --------------------------
    @staticmethod
    def generate_voucher_number():
        """Generate sequential voucher numbers per day."""
        today_str = timezone.now().strftime("%Y%m%d")
        prefix = f"SLS-{today_str}"
        last_sale = Sale.objects.filter(voucher_number__startswith=prefix).order_by('-voucher_number').first()

        if last_sale and last_sale.voucher_number.split('-')[-1].isdigit():
            last_number = int(last_sale.voucher_number.split('-')[-1])
        else:
            last_number = 0

        new_number = last_number + 1
        return f"{prefix}-{new_number:04d}"

    def save(self, *args, **kwargs):
        if not self.voucher_number:
            self.voucher_number = self.generate_voucher_number()
        super().save(*args, **kwargs)

class SaleItem(models.Model):
    SALE_TYPE_CHOICES = [
        ("unit", "Unit"),
        ("carton", "Carton"),
    ]
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="items")
    medicine = models.ForeignKey("Medicine", on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    # price saved at sale time (unit price)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    sale_type = models.CharField(max_length=10, choices=SALE_TYPE_CHOICES, default="unit")

    def __str__(self):
        return f"{self.medicine.item_name} ({self.quantity} {self.sale_type})"

    def __str__(self):
        return f"{self.quantity} x {self.medicine.brand_name} @ {self.price}"
    

class Setting(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # discount applied globally (e.g., default sale discount)
    discount = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0.00,
        null=True,
        blank=True,
        help_text="Default discount percentage (0–100)"
    )

    # threshold for low-stock warnings
    low_stock_threshold = models.IntegerField(
        default=10,
        null=True,
        blank=True,
        help_text="Default low-stock threshold for medicines"
    )

    # expiry reminder configuration
    expired_date = models.IntegerField(
        default=30,
        null=True,
        blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Settings (Discount: {self.discount}%, Threshold: {self.low_stock_threshold})"

# def today():
#     return now().date()
# class Refill(models.Model):
#     id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
#     medicine = models.ForeignKey("Medicine", on_delete=models.CASCADE, related_name="refills")
#     department = models.ForeignKey("Department", on_delete=models.SET_NULL, null=True)

#     batch_no = models.CharField(max_length=100)
#     manufacture_date = models.DateField()
#     expire_date = models.DateField()
#     price = models.DecimalField(
#         max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.00"))]
#     )
#     quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])

#     refill_date = models.DateField(default=today)
#     created_at = models.DateTimeField(auto_now_add=True)
#     created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

#     def __str__(self):
#         return f"Refill for {self.medicine.brand_name} (Batch {self.batch_no})"
