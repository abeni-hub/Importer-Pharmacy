from rest_framework import viewsets, permissions, filters
from django_filters.rest_framework import DjangoFilterBackend , FilterSet, CharFilter
from .models import Medicine, Sale, Department , SaleItem , Setting 
from .serializers import MedicineSerializer, SaleSerializer, DepartmentSerializer ,  SaleItemSerializer , SettingSerializer
from rest_framework.response import Response
from django.utils import timezone
from rest_framework.decorators import action , api_view
from datetime import timedelta
from django.db import transaction
from rest_framework import status
from django.db.models import Sum, Count , F , Avg , FloatField
from django.utils.timezone import now 
from .pagination import CustomPagination 
from django.core.paginator import Paginator
from django.db.models.functions import TruncDate
from decimal import Decimal
from django.shortcuts import get_object_or_404
from django.db.models import Q
import pandas as pd
from openpyxl import Workbook
from io import BytesIO
from django.http import HttpResponse


class DepartmentViewSet(viewsets.ModelViewSet):
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ['code','name']
    search_fields = ['code','name']
    ordering_fields = ['name', 'id']
    ordering = ['-id']
    pagination_class = CustomPagination

class MedicineFilter(FilterSet):
    brand_name = CharFilter(field_name="brand_name", lookup_expr="icontains")
    item_name = CharFilter(field_name="item_name", lookup_expr="icontains")
    batch_no = CharFilter(field_name="batch_no", lookup_expr="icontains")

    class Meta:
        model = Medicine
        fields = ["department", "unit", "brand_name", "item_name", "batch_no"]

class MedicineViewSet(viewsets.ModelViewSet):
    queryset = Medicine.objects.all()
    serializer_class = MedicineSerializer
    pagination_class = CustomPagination
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]

    # removed 'code_no' because you said you'll remove it from model
    filterset_class = MedicineFilter
    search_fields = ['brand_name', 'item_name', 'unit', 'batch_no']
    ordering_fields = ['expire_date','price','stock']

    # ---------------- BULK CREATE ----------------
    def create(self, request, *args, **kwargs):
        if isinstance(request.data, list):
            serializer = self.get_serializer(data=request.data, many=True)
            serializer.is_valid(raise_exception=True)
            self.perform_bulk_create(serializer)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return super().create(request, *args, **kwargs)
    def get_queryset(self):
        queryset = super().get_queryset()
        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(
                Q(brand_name__icontains=search) |
                Q(item_name__icontains=search) |
                Q(batch_no__icontains=search) |
                Q(company_name__icontains=search)
            )
        return queryset
    def perform_bulk_create(self, serializer):
        serializer.save(created_by=self.request.user)

    # ---------------- BULK UPDATE (by id) ----------------
    @action(detail=False, methods=['put'], url_path="bulk_update")
    def bulk_update(self, request):
        """
        Update multiple medicines at once using their `id`.
        Request body: list of objects each containing 'id' and the fields to update.
        """
        if not isinstance(request.data, list):
            return Response({"detail": "Expected a list of items for bulk_update."}, status=400)

        updated_ids = []
        for item in request.data:
            mid = item.get("id")
            if not mid:
                continue
            try:
                med = Medicine.objects.get(id=mid)
            except Medicine.DoesNotExist:
                continue
            # do not allow updating created_by via this endpoint
            for k, v in item.items():
                if k == "id":
                    continue
                setattr(med, k, v)
            med.save()
            updated_ids.append(str(mid))
        return Response({"updated": updated_ids}, status=200)

    # ---------------- CUSTOM ACTIONS ----------------
    @action(detail=False, methods=['get'])
    def expired(self, request):
        today = timezone.now().date()
        expired = Medicine.objects.filter(expire_date__lt=today)
        serializer = self.get_serializer(expired, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def nearly_expired(self, request):
        today = timezone.now().date()
        near_date = today + timedelta(days=30)
        nearly_expired = Medicine.objects.filter(expire_date__gte=today, expire_date__lte=near_date)
        serializer = self.get_serializer(nearly_expired, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def low_stock(self, request):
        low_stock = Medicine.objects.filter(stock__lte=10, stock__gt=0)
        serializer = self.get_serializer(low_stock, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def stock_out(self, request):
        stock_out = Medicine.objects.filter(stock=0)
        serializer = self.get_serializer(stock_out, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def stock(self, request):
        medicines = Medicine.objects.all()
        data = [{"id": str(m.id), "brand_name": m.brand_name, "department": {"code": m.department.code if m.department else None, "name": m.department.name if m.department else None}, "stock": m.stock} for m in medicines]
        return Response(data)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    @action(detail=False, methods=['get'], url_path='export-excel')
    def export_excel(self, request):
        queryset = self.get_queryset()

        df = pd.DataFrame(list(queryset.values()))
        if df.empty:
            return Response({"detail": "No medicine records found."}, status=404)

        # 🕒 Convert timezone-aware datetime columns to naive datetimes
        for col in df.select_dtypes(include=['datetime64[ns, UTC]']).columns:
            df[col] = df[col].dt.tz_localize(None)

        buffer = BytesIO()
        df.to_excel(buffer, index=False, engine='openpyxl')
        buffer.seek(0)

        response = HttpResponse(
            buffer,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = 'attachment; filename="medicines.xlsx"'
        return response
# ---------------- SALE VIEWSET ----------------
class SaleViewSet(viewsets.ModelViewSet):
    queryset = Sale.objects.all().order_by("-sale_date")
    serializer_class = SaleSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]

    ordering_fields = ['sale_date', 'total_amount']
    search_fields = ['customer_name', 'customer_phone', 'voucher_number']

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx.update({"request": self.request})
        return ctx

    def create(self, request, *args, **kwargs):
        """
        Create a new sale record.
        """
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            sale = serializer.save()
        out_serializer = self.get_serializer(sale)
        return Response(out_serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['get'], url_path='sold-medicines')
    def sold_medicines(self, request):
        """
        Returns paginated list of sold medicines filtered by customer, phone, or voucher_number.
        """
        page_number = int(request.query_params.get('pageNumber', 1))
        page_size = int(request.query_params.get('pageSize', 10))
        search = request.query_params.get('search', '').strip()
        voucher_number = request.query_params.get('voucher_number', '').strip()

        # 🔍 Base queryset
        sales = Sale.objects.all().order_by('-sale_date')

        # Combined filtering logic
        filters_q = Q()

        if search:
            filters_q |= (
                Q(customer_name__icontains=search) |
                Q(customer_phone__icontains=search) |
                Q(voucher_number__icontains=search)
            )

        if voucher_number:
            filters_q &= Q(voucher_number__icontains=voucher_number)

        if filters_q:
            sales = sales.filter(filters_q)

        # ✅ Avoid duplicates
        sales = sales.distinct()

        paginator = Paginator(sales, page_size)
        current_page = paginator.get_page(page_number)

        serializer = self.get_serializer(current_page, many=True)

        return Response({
            "page": page_number,
            "total_pages": paginator.num_pages,
            "total_items": paginator.count,
            "results": serializer.data
        }, status=status.HTTP_200_OK)
    
    @action(detail=False, methods=['get'], url_path='export-excel')
    def export_excel(self, request):
        queryset = self.get_queryset()

        df = pd.DataFrame(list(queryset.values()))
        if df.empty:
            return Response({"detail": "No sales records found."}, status=404)

        # 🕒 Convert timezone-aware datetime columns to naive datetimes
        for col in df.select_dtypes(include=['datetime64[ns, UTC]']).columns:
            df[col] = df[col].dt.tz_localize(None)

        buffer = BytesIO()
        df.to_excel(buffer, index=False, engine='openpyxl')
        buffer.seek(0)

        response = HttpResponse(
            buffer,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = 'attachment; filename="sales.xlsx"'
        return response

    # Export sold medicines (SaleItem) to Excel (downloadable)
    @action(detail=False, methods=['get'], url_path='export-excel')
    def export_excel(self, request):
        items = SaleItem.objects.select_related('medicine', 'sale').all()

        data = [{
            "voucher_number": item.sale.voucher_number,
            "customer": item.sale.customer_name,
            "medicine": item.medicine.brand_name if item.medicine else None,
            "quantity": item.quantity,
            "unit_price": float(item.price),
            "total_price": float(item.price) * item.quantity,
            "sale_date": item.sale.sale_date
        } for item in items]

        if not data:
            return Response({"detail": "No sold medicine records found."}, status=404)

        df = pd.DataFrame(data)

        # 🕒 Convert timezone-aware datetime columns to naive datetimes
        for col in df.select_dtypes(include=['datetime64[ns, UTC]']).columns:
            df[col] = df[col].dt.tz_localize(None)

        buffer = BytesIO()
        df.to_excel(buffer, index=False, engine='openpyxl')
        buffer.seek(0)

        response = HttpResponse(
            buffer,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        response['Content-Disposition'] = 'attachment; filename="sold_medicines.xlsx"'
        return response
class DashboardViewSet(viewsets.ViewSet):
    """
    Dashboard API: Provides stock, sales, department, and profit summaries
    """

    # ------------------- OVERVIEW -------------------
    @action(detail=False, methods=["get"])
    def overview(self, request):
        today = now().date()
        near_expiry_threshold = today + timedelta(days=30)

        # Compute total stock (in units)
        total_medicines = Medicine.objects.count()

        # Calculate total available units for each medicine
        medicines = Medicine.objects.all()
        low_stock = 0
        stock_out = 0

        for med in medicines:
            total_units = (med.stock_carton * (med.units_per_carton or 0)) + (med.stock_in_unit or 0)
            if total_units <= 0:
                stock_out += 1
            elif total_units <= 10:
                low_stock += 1

        expired = Medicine.objects.filter(expire_date__lt=today).count()
        near_expiry = Medicine.objects.filter(
            expire_date__gte=today, expire_date__lte=near_expiry_threshold
        ).count()

        # --- Sales summaries ---
        today_sales_qty = (
            SaleItem.objects.filter(sale__sale_date__date=today)
            .aggregate(total=Sum("quantity"))
            .get("total") or 0
        )
        total_sales_qty = SaleItem.objects.aggregate(total=Sum("quantity")).get("total") or 0

        revenue_today = (
            Sale.objects.filter(sale_date__date=today)
            .aggregate(revenue=Sum("total_amount"))
            .get("revenue") or 0
        )
        total_revenue = Sale.objects.aggregate(revenue=Sum("total_amount")).get("revenue") or 0

        # --- Profit summaries ---
        total_profit = 0
        for med in medicines:
            total_units = (med.stock_carton * (med.units_per_carton or 0)) + (med.stock_in_unit or 0)
            profit_per_unit = float(med.price - med.buying_price)
            total_profit += profit_per_unit * total_units

        today_profit = (
            SaleItem.objects.annotate(
                profit=(F("medicine__price") - F("medicine__buying_price")) * F("quantity")
            )
            .filter(sale__sale_date__date=today)
            .aggregate(total=Sum("profit"))
            .get("total")
            or 0
        )

        # --- Top 5 selling medicines ---
        top_selling = (
            SaleItem.objects.values("medicine__brand_name")
            .annotate(total_sold=Sum("quantity"))
            .order_by("-total_sold")[:5]
        )

        # --- Department stats ---
        department_stats = (
            Medicine.objects.values("department__name")
            .annotate(
                total=Count("id"),
                total_profit=Sum(
                    (F("price") - F("buying_price"))
                    * ((F("stock_carton") * F("units_per_carton")) + F("stock_in_unit")),
                    output_field=FloatField(),
                ),
            )
            .order_by("department__name")
        )

        return Response({
            "stock": {
                "total_medicines": total_medicines,
                "low_stock": low_stock,
                "stock_out": stock_out,
                "expired": expired,
                "near_expiry": near_expiry,
            },
            "sales": {
                "today_sales_qty": today_sales_qty,
                "total_sales_qty": total_sales_qty,
                "revenue_today": revenue_today,
                "total_revenue": total_revenue,
            },
            "profit": {
                "today_profit": today_profit,
                "total_profit": round(total_profit, 2),
            },
            "top_selling": list(top_selling),
            "departments": list(department_stats),
        })

    # ------------------- ANALYTICS -------------------
    @action(detail=False, methods=["get"])
    def analytics(self, request):
        today = now().date()
        last_week = today - timedelta(days=7)
        near_expiry_threshold = today + timedelta(days=30)

        medicines = Medicine.objects.all()

        # Compute inventory value & profit based on cartons + units
        inventory_value = 0
        total_profit = 0
        for med in medicines:
            total_units = (med.stock_carton * (med.units_per_carton or 0)) + (med.stock_in_unit or 0)
            inventory_value += float(med.price) * total_units
            total_profit += float(med.price - med.buying_price) * total_units

        total_revenue = Sale.objects.aggregate(total=Sum("total_amount")).get("total") or 0
        total_transactions = Sale.objects.count()
        avg_order_value = Sale.objects.aggregate(avg=Avg("total_amount")).get("avg") or 0

        profit_margin = (
            (Decimal(str(total_profit)) / Decimal(str(inventory_value)) * 100)
            if inventory_value > 0 else Decimal(0)
        )

        # --- Sales trend (last 7 days) ---
        sales_trend = (
            Sale.objects.filter(sale_date__date__gte=last_week)
            .annotate(day=TruncDate("sale_date"))
            .values("day")
            .annotate(total_sales=Sum("total_amount"))
            .order_by("day")
        )

        # --- Inventory by department ---
        inventory_by_category = (
            Medicine.objects.values("department__name")
            .annotate(
                value=Sum(
                    (F("stock_carton") * F("units_per_carton") + F("stock_in_unit")) * F("price"),
                    output_field=FloatField(),
                ),
                profit=Sum(
                    (F("price") - F("buying_price"))
                    * (F("stock_carton") * F("units_per_carton") + F("stock_in_unit")),
                    output_field=FloatField(),
                ),
            )
            .order_by("department__name")
        )

        # --- Top selling medicines ---
        top_selling = (
            SaleItem.objects.values("medicine__brand_name")
            .annotate(total_sold=Sum("quantity"))
            .order_by("-total_sold")[:5]
        )

        # --- Stock alerts ---
        low_stock = []
        stock_out = []
        near_expiry = []

        for med in medicines:
            total_units = (med.stock_carton * (med.units_per_carton or 0)) + (med.stock_in_unit or 0)
            if total_units <= 0:
                stock_out.append({"brand_name": med.brand_name})
            elif total_units <= 10:
                low_stock.append({"brand_name": med.brand_name, "stock": total_units})
            if today <= med.expire_date <= near_expiry_threshold:
                near_expiry.append({"brand_name": med.brand_name, "expire_date": med.expire_date})

        # Weekly summary
        week_sales = (
            Sale.objects.filter(sale_date__date__gte=last_week)
            .aggregate(total=Sum("total_amount"))
            .get("total") or 0
        )
        week_transactions = Sale.objects.filter(sale_date__date__gte=last_week).count()

        total_products = medicines.count()
        inventory_turnover = (
            float(total_revenue) / float(inventory_value) if inventory_value > 0 else 0
        )

        return Response({
            "summary": {
                "total_revenue": total_revenue,
                "total_transactions": total_transactions,
                "avg_order_value": avg_order_value,
                "inventory_value": inventory_value,
                "total_profit": round(total_profit, 2),
                "profit_margin": round(profit_margin, 2),
            },
            "sales_trend": list(sales_trend),
            "inventory_by_category": list(inventory_by_category),
            "top_selling": list(top_selling),
            "stock_alerts": {
                "low_stock": low_stock,
                "stock_out": stock_out,
                "near_expiry": near_expiry,
            },
            "weekly_summary": {
                "week_sales": week_sales,
                "transactions": week_transactions,
            },
            "inventory_health": {
                "total_products": total_products,
                "low_stock": len(low_stock),
                "near_expiry": len(near_expiry),
                "stock_out": len(stock_out),
            },
            "performance_metrics": {
                "inventory_turnover": round(inventory_turnover, 2),
            },
        })

    # ------------------- PROFIT SUMMARY -------------------
    @action(detail=False, methods=["get"])
    def profit_summary(self, request):
        """Fetch total daily, weekly, and monthly profit."""
        today = now().date()
        week_ago = today - timedelta(days=7)
        month_ago = today - timedelta(days=30)

        def calc_profit(qs):
            return (
                qs.annotate(
                    profit=(F("medicine__price") - F("medicine__buying_price")) * F("quantity")
                )
                .aggregate(total=Sum("profit"))
                .get("total")
                or 0
            )

        daily_profit = calc_profit(SaleItem.objects.filter(sale__sale_date__date=today))
        weekly_profit = calc_profit(SaleItem.objects.filter(sale__sale_date__date__gte=week_ago))
        monthly_profit = calc_profit(SaleItem.objects.filter(sale__sale_date__date__gte=month_ago))

        return Response({
            "daily_profit": round(daily_profit, 2),
            "weekly_profit": round(weekly_profit, 2),
            "monthly_profit": round(monthly_profit, 2),
        })
    
class SettingViewSet(viewsets.ModelViewSet):
    queryset = Setting.objects.all()
    serializer_class = SettingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        """Always return the singleton Setting instance."""
        setting, created = Setting.objects.get_or_create(
            defaults={
                "discount": 0.00,
                "low_stock_threshold": 10,
                "expired_date": "09:00:00",
            }
        )
        return setting

    def list(self, request, *args, **kwargs):
        """GET /api/settings/ → return the single Setting"""
        setting = self.get_object()
        serializer = self.get_serializer(setting)
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
        """POST /api/settings/ → update existing instead of creating new"""
        setting = self.get_object()
        serializer = self.get_serializer(setting, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def update(self, request, *args, **kwargs):
        """PUT /api/settings/ → update the Setting"""
        setting = self.get_object()
        serializer = self.get_serializer(setting, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def destroy(self, request, *args, **kwargs):
        """Prevent deletion of Setting"""
        return Response({"detail": "Deleting settings is not allowed."}, status=status.HTTP_405_METHOD_NOT_ALLOWED)