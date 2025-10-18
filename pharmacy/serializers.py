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
            "stock_unit",
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
    items = SaleItemSerializer(many=True, read_only=True)
    input_items = SaleCreateItemSerializer(many=True, write_only=True, required=True)
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
            "id", "sale_date", "base_price", "discounted_amount", "total_amount",
            "items", "sold_by", "discounted_by"
        ]

    # ------------------------------
    # VALIDATION
    # ------------------------------
    def validate_discount_percentage(self, value):
        if value is None:
            return Decimal("0.00")
        if value < 0 or value > 100:
            raise serializers.ValidationError("discount_percentage must be between 0 and 100.")
        return value

    def validate(self, attrs):
        items = attrs.get("input_items", [])
        if not items:
            raise serializers.ValidationError({"input_items": "At least one item is required to create a sale."})
        return attrs

    # ------------------------------
    # CORE BUSINESS LOGIC
    # ------------------------------
    def create_sale_items_and_adjust_stock(self, sale, items, request_user):
        created_items = []

        for idx, it in enumerate(items):
            med_id = it.get("medicine")
            qty = int(it.get("quantity"))
            sale_type = it.get("sale_type", "unit")
            provided_price = it.get("price", None)

            # Fetch medicine with lock
            try:
                medicine = Medicine.objects.select_for_update().get(id=med_id)
            except Medicine.DoesNotExist:
                raise serializers.ValidationError({
                    "input_items": f"Medicine {med_id} does not exist (item index {idx})."
                })

            # Calculate total stock in units
            total_units = (medicine.stock_carton * medicine.units_per_carton) + medicine.stock_in_unit

            # ------------------------------
            # Handle sale by CARTON
            # ------------------------------
            if sale_type == "carton":
                if medicine.stock_carton < qty:
                    raise serializers.ValidationError({
                        "input_items": f"Not enough cartons for {medicine.brand_name}. Available: {medicine.stock_carton}, requested: {qty}."
                    })
                # Deduct cartons and equivalent units
                medicine.stock_carton -= qty
                deducted_units = qty * medicine.units_per_carton
                total_units -= deducted_units

            # ------------------------------
            # Handle sale by UNIT
            # ------------------------------
            elif sale_type == "unit":
                if total_units < qty:
                    raise serializers.ValidationError({
                        "input_items": f"Not enough stock for {medicine.brand_name}. Available: {total_units} units, requested: {qty} units."
                    })

                # If enough units in stock, deduct directly
                if medicine.stock_in_unit >= qty:
                    medicine.stock_in_unit -= qty
                else:
                    # Borrow from cartons
                    needed = qty - medicine.stock_in_unit
                    cartons_needed = (needed + medicine.units_per_carton - 1) // medicine.units_per_carton

                    if medicine.stock_carton < cartons_needed:
                        raise serializers.ValidationError({
                            "input_items": f"Not enough stock for {medicine.brand_name}. Available: {total_units} units, requested: {qty} units."
                        })

                    # Deduct cartons and recalculate unit stock
                    medicine.stock_carton -= cartons_needed
                    medicine.stock_in_unit += cartons_needed * medicine.units_per_carton - qty

            # ------------------------------
            # Determine price and create SaleItem
            # ------------------------------
            unit_price = Decimal(provided_price) if provided_price is not None else medicine.price
            sale_item = SaleItem.objects.create(
                sale=sale,
                medicine=medicine,
                quantity=qty,
                price=unit_price
            )

            medicine.save()
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

        # Create base Sale record
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

        # Process items and update stocks
        created_items = self.create_sale_items_and_adjust_stock(sale, items, user)

        # Compute total prices
        base_price = sum(Decimal(i.quantity) * i.price for i in created_items)
        discount_pct = validated_data.get("discount_percentage", Decimal("0.00")) or Decimal("0.00")
        discounted_amount = (base_price * (discount_pct / Decimal("100.00"))).quantize(Decimal("0.01"))
        total_amount = (base_price - discounted_amount).quantize(Decimal("0.01"))

        # Update Sale totals
        sale.base_price = base_price.quantize(Decimal("0.01"))
        sale.discounted_amount = discounted_amount
        sale.total_amount = total_amount

        # Set discounted_by if applicable
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
