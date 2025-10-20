from rest_framework import serializers
from .models import Medicine, Sale, Department, SaleItem , Setting
from decimal import Decimal
from django.db import transaction
from django.utils.timezone import now


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = ['id', 'code', 'name']

# class MedicineDepartmentSimpleSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = Department
#         fields = ['code', 'name']

class MedicineSerializer(serializers.ModelSerializer):

    profit_per_item = serializers.SerializerMethodField()
    total_profit = serializers.SerializerMethodField()
    total_stock_units = serializers.SerializerMethodField(read_only=True)
    is_out_of_stock = serializers.SerializerMethodField()
    is_expired = serializers.SerializerMethodField()
    is_nearly_expired = serializers.SerializerMethodField()
    # refill_count = serializers.SerializerMethodField()

    # ✅ Show both value and label for unit
    unit_display = serializers.CharField(source='get_unit_display', read_only=True)

    # ✅ Nested serializer for read
    department = DepartmentSerializer(read_only=True)

    # ✅ Use department_id for write
    department_id = serializers.PrimaryKeyRelatedField(
        queryset=Department.objects.all(),
        write_only=True,
        source='department'
    )

    class Meta:
        model = Medicine
        fields = [
            "id",
            "brand_name",
            "item_name",
            "batch_no",
            "manufacture_date",
            "expire_date",
            "buying_price",   # ✅ ADD THIS LINE
            "price",
            "total_profit",
            "profit_per_item",
            "stock_carton",
            "units_per_carton",
            "stock_in_unit",
            "total_stock_units",
            "low_stock_threshold",
            "unit",
            "unit_display",
            "company_name",
            "FSNO",
            "department",
            "department_id",
            "attachment",
            "is_out_of_stock",
            "is_expired",
            "is_nearly_expired",
            #"refill_count",
            "created_at",
            "updated_at",
            "created_by",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]
    def get_total_stock_units(self, obj):
        return obj.total_stock_units
    def get_is_out_of_stock(self, obj):
        return obj.is_out_of_stock()

    def get_is_expired(self, obj):
        return obj.is_expired()

    def get_is_nearly_expired(self, obj):
        return obj.is_nearly_expired()
    
    def get_profit_per_item(self, obj):
        return obj.profit_per_item()

    def get_total_profit(self, obj):
        return obj.total_profit()

    # ✅ Ensure department is refreshed in response
    def create(self, validated_data):
        instance = super().create(validated_data)
        # Re-fetch related objects for full nested serialization
        return Medicine.objects.select_related('department').get(pk=instance.pk)

    def update(self, instance, validated_data):
        instance = super().update(instance, validated_data)
        return Medicine.objects.select_related('department').get(pk=instance.pk)


class SaleItemSerializer(serializers.ModelSerializer):
    medicine_name = serializers.CharField(source="medicine.brand_name", read_only=True)
    batch_no = serializers.CharField(source="medicine.batch_no", read_only=True)
    expire_date = serializers.DateField(source="medicine.expire_date", read_only=True)
    total_price = serializers.SerializerMethodField()

    class Meta:
        model = SaleItem
        fields = ["id", "medicine", "medicine_name","batch_no","expire_date", "quantity", "price", "total_price"]
        read_only_fields = ["id", "medicine_name", "batch_no","expire_date","total_price"]

    def get_total_price(self, obj):
        return str(Decimal(obj.quantity) * obj.price)


class SaleCreateItemSerializer(serializers.Serializer):
    """
    Serializer used inside SaleSerializer for incoming sale items.
    Accepts medicine (id), quantity, optionally price (unit price). If price not provided,
    medicine.price (current price) will be used.
    """
    medicine = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1)
    batch_no = serializers.CharField(source='medicine.batch_no', read_only=True)
    expire_date = serializers.DateField(source='medicine.expire_date', read_only=True)
    price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)


class SaleSerializer(serializers.ModelSerializer):
    items = serializers.SerializerMethodField(read_only=True)
    input_items = serializers.ListField(write_only=True, required=True)
    sold_by_username = serializers.CharField(source="sold_by.username", read_only=True)
    discounted_by_username = serializers.CharField(source="discounted_by.username", read_only=True)

    class Meta:
        model = Sale
        fields = [
            "id", "sold_by", "sold_by_username",
            "customer_name", "customer_phone", "sale_date",
            "payment_method", "discount_percentage",
            "base_price", "discounted_amount", "total_amount",
            "discounted_by", "discounted_by_username",
            "items", "input_items",
        ]
        read_only_fields = [
            "id", "sale_date", "base_price", "discounted_amount",
            "total_amount", "items", "sold_by", "discounted_by"
        ]

    def get_items(self, obj):
        return [
            {
                "medicine": item.medicine.item_name,

                "quantity": item.quantity,
                "price": str(item.price),
                "batch_no": item.medicine.batch_no,
                "expire_date": item.medicine.expire_date,
                "quantity": item.quantity,
                "price": str(item.price),
                "sale_type": item.sale_type,
                "unit_type": "carton" if item.sale_type == "carton" else "unit"
            }
            for item in obj.items.all()
        ]

    # ------------------------------
    # VALIDATION
    # ------------------------------
    def validate_discount_percentage(self, value):
        if value is None:
            return Decimal("0.00")
        if value < 0 or value > 100:
            raise serializers.ValidationError("Discount percentage must be between 0 and 100.")
        return value

    def validate(self, attrs):
        items = attrs.get("input_items", [])
        if not items:
            raise serializers.ValidationError({"input_items": "At least one item is required to create a sale."})
        return attrs

    # ------------------------------
    # CORE STOCK LOGIC
    # ------------------------------
    def adjust_stock(self, medicine, qty, sale_type):
        """
        Adjust stock levels for a medicine during sale.
        Keeps carton and unit values consistent.
        """

        units_per_carton = medicine.units_per_carton or 0
        total_units_before = (medicine.stock_carton * units_per_carton) + medicine.stock_in_unit

        # Determine required units
        required_units = qty * units_per_carton if sale_type == "carton" else qty

        # Validate stock
        if total_units_before < required_units:
            raise serializers.ValidationError({
                "input_items": f"Not enough stock for {medicine.brand_name}. "
                               f"Available: {total_units_before} units, requested: {required_units} units."
            })

        # ------------------------------
        # SALE BY CARTON
        # ------------------------------
        if sale_type == "carton":
            # Reduce carton count
            medicine.stock_carton -= qty
            # Also reduce loose units equivalent to cartons
            medicine.stock_in_unit -= (qty * units_per_carton)
            # Prevent negatives
            if medicine.stock_in_unit < 0:
                medicine.stock_in_unit = 0

        # ------------------------------
        # SALE BY UNIT
        # ------------------------------
        elif sale_type == "unit":
            if medicine.stock_in_unit >= qty:
                # Enough loose units
                medicine.stock_in_unit -= qty
            else:
                # Break cartons if needed
                needed_units = qty - medicine.stock_in_unit
                cartons_to_break = (needed_units + units_per_carton - 1) // units_per_carton

                if medicine.stock_carton < cartons_to_break:
                    raise serializers.ValidationError({
                        "input_items": f"Not enough cartons to break for {medicine.brand_name}. "
                                       f"Available cartons: {medicine.stock_carton}."
                    })

                medicine.stock_carton -= cartons_to_break
                borrowed_units = cartons_to_break * units_per_carton
                medicine.stock_in_unit = borrowed_units - needed_units

        # ------------------------------
        # FINAL UPDATE AND SAVE
        # ------------------------------
        total_units_after = (medicine.stock_carton * units_per_carton) + medicine.stock_in_unit
        medicine.is_out_of_stock = total_units_after <= 0
        medicine.save()

    # ------------------------------
    # CREATE SALE ITEMS & UPDATE STOCK
    # ------------------------------
    def create_sale_items_and_adjust_stock(self, sale, items, request_user):
        created_items = []

        for idx, item in enumerate(items):
            med_id = item.get("medicine")
            qty = int(item.get("quantity"))
            sale_type = item.get("sale_type", "unit")
            provided_price = item.get("price", None)

            try:
                medicine = Medicine.objects.select_for_update().get(id=med_id)
            except Medicine.DoesNotExist:
                raise serializers.ValidationError({
                    "input_items": f"Medicine {med_id} does not exist (item index {idx})."
                })

            # Adjust stock
            self.adjust_stock(medicine, qty, sale_type)

            # Use provided price or default
            unit_price = Decimal(provided_price) if provided_price is not None else medicine.price

            # Create SaleItem
            sale_item = SaleItem.objects.create(
                sale=sale,
                medicine=medicine,
                quantity=qty,
                price=unit_price,

                sale_type=sale_type
            )

            created_items.append(sale_item)

        return created_items

    # ------------------------------
    # MAIN CREATE METHOD
    # ------------------------------
    @transaction.atomic
    def create(self, validated_data):
        request = self.context.get("request")
        user = getattr(request, "user", None)
        items = validated_data.pop("input_items", [])

        # Create sale record
        sale = Sale.objects.create(
            sold_by=user,
            customer_name=validated_data.get("customer_name"),
            customer_phone=validated_data.get("customer_phone"),
            payment_method=validated_data.get("payment_method", "cash"),
            discount_percentage=validated_data.get("discount_percentage", Decimal("0.00")),
            base_price=Decimal("0.00"),
            discounted_amount=Decimal("0.00"),
            total_amount=Decimal("0.00"),
        )

        # Create sale items and adjust stock
        created_items = self.create_sale_items_and_adjust_stock(sale, items, user)

        # Compute totals
        base_price = sum(Decimal(i.quantity) * i.price for i in created_items)
        discount_pct = validated_data.get("discount_percentage", Decimal("0.00")) or Decimal("0.00")
        discounted_amount = (base_price * (discount_pct / Decimal("100.00"))).quantize(Decimal("0.01"))
        total_amount = (base_price - discounted_amount).quantize(Decimal("0.01"))

        # Update sale totals
        sale.base_price = base_price.quantize(Decimal("0.01"))
        sale.discounted_amount = discounted_amount
        sale.total_amount = total_amount

        if discount_pct > 0 and user and user.is_authenticated:
            sale.discounted_by = user

        sale.save()
        return sale
class SettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Setting
        fields = ["id", "discount", "low_stock_threshold", "expired_date", "updated_at"]
        read_only_fields = ["id", "updated_at"]
# class RefillSerializer(serializers.ModelSerializer):
#     medicine_name = serializers.CharField(source="medicine.brand_name", read_only=True)
#     department_name = serializers.CharField(source="department.name", read_only=True)
#     created_by_username = serializers.CharField(source="created_by.username", read_only=True)

#     class Meta:
#         model = Refill
#         fields = [
#             "id",
#             "medicine",
#             "medicine_name",
#             "department",
#             "department_name",
#             "batch_no",
#             "manufacture_date",
#             "expire_date",
#             "price",
#             "quantity",
#             "refill_date",
#             "created_at",
#             "created_by",
#             "created_by_username",
#         ]
#         read_only_fields = ["id", "created_at", "created_by"]
