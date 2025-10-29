from rest_framework import viewsets, permissions, filters, status
from django_filters.rest_framework import DjangoFilterBackend, FilterSet, CharFilter
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils import timezone
from django.core.cache import cache
from django.views.decorators.cache import cache_page
from django.utils.decorators import method_decorator
from django.db.models import Q, F, Sum, Count, Avg, FloatField , ExpressionWrapper, DecimalField
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.http import HttpResponse
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from openpyxl import Workbook
import pandas as pd

from .models import Medicine, Sale, Department, SaleItem, Setting
from .serializers import (
    MedicineSerializer,
    SaleSerializer,
    DepartmentSerializer,
    SaleItemSerializer,
    SettingSerializer,
)
from .pagination import CustomPagination
from django.db.models.functions import TruncDate
from django.utils.timezone import now


# -------------------- DEPARTMENT --------------------
class DepartmentViewSet(viewsets.ModelViewSet):
    queryset = Department.objects.all()
    serializer_class = DepartmentSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_fields = ["code", "name"]
    search_fields = ["code", "name"]
    ordering_fields = ["name", "id"]
    ordering = ["-id"]
    pagination_class = CustomPagination


# -------------------- MEDICINE FILTER --------------------
class MedicineFilter(FilterSet):
    brand_name = CharFilter(field_name="brand_name", lookup_expr="icontains")
    item_name = CharFilter(field_name="item_name", lookup_expr="icontains")
    batch_no = CharFilter(field_name="batch_no", lookup_expr="icontains")

    class Meta:
        model = Medicine
        fields = ["department", "unit", "brand_name", "item_name", "batch_no"]


# -------------------- MEDICINE --------------------
class MedicineViewSet(viewsets.ModelViewSet):
    queryset = Medicine.objects.all()
    serializer_class = MedicineSerializer
    pagination_class = CustomPagination
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = MedicineFilter
    search_fields = ["brand_name", "item_name", "unit", "batch_no"]
    ordering_fields = ["expire_date", "price", "stock"]

    # ------------------- ALERT (Expired + Low Stock) -------------------
   # ---------------- Export Excel ----------------
    @action(detail=False, methods=["get"], url_path="export-excel")
    def export_excel(self, request):
        queryset = self.get_queryset()
        df = pd.DataFrame(list(queryset.values()))

        if df.empty:
            return Response({"detail": "No medicine records found."}, status=404)

        # Handle timezone-aware columns
        for col in df.select_dtypes(include=["datetime64[ns, UTC]"]).columns:
            df[col] = df[col].dt.tz_localize(None)

        buffer = BytesIO()
        df.to_excel(buffer, index=False, engine="openpyxl")
        buffer.seek(0)

        response = HttpResponse(
            buffer,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = 'attachment; filename="medicines.xlsx"'
        return response

    # ---------------- Combined Alerts (Expired + Low Stock) ----------------
    @method_decorator(cache_page(60 * 5))  # Cache for 5 minutes
    @action(detail=False, methods=["get"], url_path="alerts")
    def alerts(self, request):
        """
        Returns medicines that are either expired OR have stock below low_stock_threshold.
        Cached for 5 minutes using Redis to reduce DB hits.
        """
        today = date.today()

        # Filter for expired or low stock
        queryset = Medicine.objects.filter(
            Q(expire_date__lte=today) |
            Q(stock_in_unit__lte=F("low_stock_threshold")) |
            Q(stock_carton__lte=F("low_stock_threshold"))
        ).only(
            "id",
            "brand_name",
            "item_name",
            "batch_no",
            "stock_in_unit",
            "stock_carton",
            "units_per_carton",
            "low_stock_threshold",
            "expire_date",
            "price",
            "buying_price",
        )

        serializer = self.get_serializer(queryset, many=True)

        expired_count = queryset.filter(expire_date__lte=today).count()
        low_stock_count = queryset.filter(
            Q(stock_in_unit__lte=F("low_stock_threshold")) |
            Q(stock_carton__lte=F("low_stock_threshold"))
        ).count()

        return Response(
            {
                "alert": queryset.exists(),
                "expired_count": expired_count,
                "low_stock_count": low_stock_count,
                "total_alerts": queryset.count(),
                "message": f"{expired_count} expired and {low_stock_count} low-stock medicines found.",
                "data": serializer.data,
            },
            status=status.HTTP_200_OK,
        )

    # ---------------- Simple Analytics Summary ----------------
    @action(detail=False, methods=["get"], url_path="analytics")
    def analytics(self, request):
        today = date.today()
        total_medicines = Medicine.objects.count()
        expired_count = Medicine.objects.filter(expire_date__lte=today).count()
        low_stock_count = Medicine.objects.filter(
            Q(stock_in_unit__lte=F("low_stock_threshold")) |
            Q(stock_carton__lte=F("low_stock_threshold"))
        ).count()

        total_inventory_value = Medicine.objects.aggregate(
            total=Sum(
                (F("stock_carton") * F("units_per_carton") + F("stock_in_unit")) * F("price"),
                output_field=FloatField(),
            )
        )["total"] or 0

        return Response(
            {
                "summary": {
                    "total_medicines": total_medicines,
                    "expired_medicines": expired_count,
                    "low_stock_medicines": low_stock_count,
                    "total_inventory_value": total_inventory_value,
                }
            },
            status=status.HTTP_200_OK,
        )
    # ---------------- BULK CREATE ----------------
    def create(self, request, *args, **kwargs):
        if isinstance(request.data, list):
            serializer = self.get_serializer(data=request.data, many=True)
            serializer.is_valid(raise_exception=True)
            self.perform_bulk_create(serializer)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return super().create(request, *args, **kwargs)

    def perform_bulk_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def get_queryset(self):
        queryset = super().get_queryset()
        search = self.request.query_params.get("search", None)
        if search:
            queryset = queryset.filter(
                Q(brand_name__icontains=search)
                | Q(item_name__icontains=search)
                | Q(batch_no__icontains=search)
                | Q(company_name__icontains=search)
            )
        return queryset

    # ---------------- BULK UPDATE ----------------
    @action(detail=False, methods=["put"], url_path="bulk_update")
    def bulk_update(self, request):
        if not isinstance(request.data, list):
            return Response(
                {"detail": "Expected a list of items for bulk_update."}, status=400
            )

        updated_ids = []
        for item in request.data:
            mid = item.get("id")
            if not mid:
                continue
            try:
                med = Medicine.objects.get(id=mid)
            except Medicine.DoesNotExist:
                continue
            for k, v in item.items():
                if k == "id":
                    continue
                setattr(med, k, v)
            med.save()
            updated_ids.append(str(mid))
        return Response({"updated": updated_ids}, status=200)

    # # ---------------- CUSTOM STOCK & EXPORT ----------------
    # @action(detail=False, methods=["get"], url_path="export-excel")
    # def export_excel(self, request):
    #     queryset = self.get_queryset()
    #     df = pd.DataFrame(list(queryset.values()))
    #     if df.empty:
    #         return Response({"detail": "No medicine records found."}, status=404)

    #     for col in df.select_dtypes(include=["datetime64[ns, UTC]"]).columns:
    #         df[col] = df[col].dt.tz_localize(None)

    #     buffer = BytesIO()
    #     df.to_excel(buffer, index=False, engine="openpyxl")
    #     buffer.seek(0)
    #     response = HttpResponse(
    #         buffer,
    #         content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    #     )
    #     response["Content-Disposition"] = 'attachment; filename="medicines.xlsx"'
    #     return response


# -------------------- SALE --------------------
class SaleViewSet(viewsets.ModelViewSet):
    queryset = Sale.objects.all().order_by("-sale_date")
    serializer_class = SaleSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = CustomPagination
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    ordering_fields = ["sale_date", "total_amount"]
    search_fields = ["customer_name", "customer_phone", "voucher_number"]

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx.update({"request": self.request})
        return ctx

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            sale = serializer.save()
        return Response(self.get_serializer(sale).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"], url_path="sold-medicines")
    def sold_medicines(self, request):
        page_number = int(request.query_params.get("pageNumber", 1))
        page_size = int(request.query_params.get("pageSize", 10))
        search = request.query_params.get("search", "").strip()
        voucher_number = request.query_params.get("voucher_number", "").strip()

        sales = Sale.objects.all().order_by("-sale_date")

        filters_q = Q()
        if search:
            filters_q |= (
                Q(customer_name__icontains=search)
                | Q(customer_phone__icontains=search)
                | Q(voucher_number__icontains=search)
            )
        if voucher_number:
            filters_q &= Q(voucher_number__icontains=voucher_number)
        if filters_q:
            sales = sales.filter(filters_q)

        paginator = CustomPagination()
        result_page = paginator.paginate_queryset(sales, request)
        serializer = self.get_serializer(result_page, many=True)
        return paginator.get_paginated_response(serializer.data)

    @action(detail=False, methods=["get"], url_path="export-excel")
    def export_excel(self, request):
        items = SaleItem.objects.select_related("medicine", "sale").all()
        data = [
            {
                "voucher_number": item.sale.voucher_number,
                "customer": item.sale.customer_name,
                "medicine": item.medicine.brand_name if item.medicine else None,
                "quantity": item.quantity,
                "unit_price": float(item.price),
                "total_price": float(item.price) * item.quantity,
                "sale_date": item.sale.sale_date,
            }
            for item in items
        ]
        if not data:
            return Response({"detail": "No sold medicine records found."}, status=404)
        df = pd.DataFrame(data)
        for col in df.select_dtypes(include=["datetime64[ns, UTC]"]).columns:
            df[col] = df[col].dt.tz_localize(None)
        buffer = BytesIO()
        df.to_excel(buffer, index=False, engine="openpyxl")
        buffer.seek(0)
        response = HttpResponse(
            buffer,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = 'attachment; filename="sold_medicines.xlsx"'
        return response


# -------------------- DASHBOARD --------------------
class DashboardViewSet(viewsets.ViewSet):
    """
    Dashboard API for overview, analytics, and profit summary
    """

    # ----------------- OVERVIEW SECTION -----------------
    @action(detail=False, methods=["get"])
    def overview(self, request):
        today = now().date()
        near_expiry_threshold = today + timedelta(days=30)

        total_medicines = Medicine.objects.count()

        # Use stock_in_unit as primary per-unit stock; for cartons, compute units when needed
        low_stock = Medicine.objects.filter(
            (F("stock_in_unit") + F("stock_carton") * F("units_per_carton")) <= F("low_stock_threshold"),
            (F("stock_in_unit") + F("stock_carton") * F("units_per_carton")) > 0
        ).count()

        stock_out = Medicine.objects.filter(
            (F("stock_in_unit") + F("stock_carton") * F("units_per_carton")) <= 0
        ).count()

        expired = Medicine.objects.filter(expire_date__lt=today).count()
        near_expiry = Medicine.objects.filter(
            expire_date__gte=today, expire_date__lte=near_expiry_threshold
        ).count()

        today_sales_qty = (
            SaleItem.objects.filter(sale__sale_date__date=today)
            .aggregate(total=Sum("quantity"))
            .get("total") or 0
        )
        total_sales_qty = SaleItem.objects.aggregate(total=Sum("quantity")).get("total") or 0

        revenue_today = (
            Sale.objects.filter(sale_date__date=today)
            .aggregate(revenue=Sum("total_amount"))
            .get("revenue") or Decimal("0.00")
        )
        total_revenue = Sale.objects.aggregate(revenue=Sum("total_amount")).get("revenue") or Decimal("0.00")

        return Response({
            "stock": {
                "total_medicines": total_medicines,
                "low_stock": low_stock,
                "stock_out": stock_out,
                "expired": expired,
                "near_expiry": near_expiry,
            },
            "sales": {
                "today_sales_qty": int(today_sales_qty),
                "total_sales_qty": int(total_sales_qty),
                "revenue_today": float(revenue_today),
                "total_revenue": float(total_revenue),
            },
        })


    # ----------------- ANALYTICS SECTION -----------------
    @action(detail=False, methods=["get"])
    def analytics(self, request):
        """
        Returns JSON shaped exactly like the provided AnalyticsData interface.
        URL: /pharmacy/dashboard/analytics/
        """

        today = now().date()
        last_week = today - timedelta(days=6)  # include today + 6 previous = 7 days window
        near_expiry_threshold = today + timedelta(days=30)

        # ---------- Summary ----------
        total_revenue = Sale.objects.aggregate(total=Sum("total_amount"))["total"] or Decimal("0.00")
        total_transactions = Sale.objects.count()
        avg_order_value = (total_revenue / total_transactions) if total_transactions > 0 else Decimal("0.00")

        # inventory_value: sum of (cartons*units_per_carton + units) * price
        inventory_value_agg = Medicine.objects.aggregate(
            inventory_value=Sum(
                (F("stock_carton") * F("units_per_carton") + F("stock_in_unit")) * F("price"),
                output_field=DecimalField(max_digits=30, decimal_places=2)
            )
        )["inventory_value"] or Decimal("0.00")

        summary = {
            "total_revenue": float(total_revenue),
            "total_transactions": int(total_transactions),
            "avg_order_value": float(avg_order_value),
            "inventory_value": float(inventory_value_agg),
        }

        # ---------- Sales Trend (last 7 days) ----------
        sales_trend_qs = (
            Sale.objects.filter(sale_date__date__gte=last_week)
            .annotate(day=TruncDate("sale_date"))
            .values("day")
            .annotate(total_sales=Sum("total_amount"))
            .order_by("day")
        )
        sales_trend = [
            {
                "day": entry["day"].strftime("%Y-%m-%d"),
                "total_sales": float(entry["total_sales"] or 0)
            }
            for entry in sales_trend_qs
        ]

        # If there are days with zero sales and you need all 7 days present, front-end can fill gaps.
        # (Keeping raw DB output here, matches interface expectations.)

        # ---------- Inventory by Category ----------
        inv_by_cat_qs = (
            Medicine.objects.values("department__name")
            .annotate(
                value=Sum(
                    (F("stock_carton") * F("units_per_carton") + F("stock_in_unit")) * F("price"),
                    output_field=DecimalField(max_digits=30, decimal_places=2)
                ),
                profit=Sum(
                    (F("price") - F("buying_price")) * (F("stock_carton") * F("units_per_carton") + F("stock_in_unit")),
                    output_field=DecimalField(max_digits=30, decimal_places=2)
                ),
            )
            .order_by("-value")
        )
        inventory_by_category = [
            {
                "department__name": row["department__name"] or "Uncategorized",
                "value": float(row["value"] or 0),
                "profit": float(row["profit"] or 0),
            }
            for row in inv_by_cat_qs
        ]

        # ---------- Top Selling ----------
        top_selling_qs = (
            SaleItem.objects.values("medicine__brand_name")
            .annotate(total_sold=Sum("quantity"))
            .order_by("-total_sold")[:5]
        )
        top_selling = [
            {
                "medicine__brand_name": row["medicine__brand_name"] or "Unknown",
                "total_sold": int(row["total_sold"] or 0)
            }
            for row in top_selling_qs
        ]

        # ---------- Stock Alerts ----------
        # low_stock: medicines where total_units <= low_stock_threshold and > 0
        low_stock_qs = Medicine.objects.annotate(
            total_units=(F("stock_carton") * F("units_per_carton") + F("stock_in_unit"))
        ).filter(total_units__lte=F("low_stock_threshold"), total_units__gt=0).values(
            "id", "brand_name", "item_name", "batch_no", "stock_carton", "units_per_carton", "stock_in_unit", "low_stock_threshold"
        )

        stock_out_qs = Medicine.objects.annotate(
            total_units=(F("stock_carton") * F("units_per_carton") + F("stock_in_unit"))
        ).filter(total_units__lte=0).values(
            "id", "brand_name", "item_name", "batch_no", "stock_carton", "units_per_carton", "stock_in_unit"
        )

        near_expiry_qs = Medicine.objects.filter(
            expire_date__gte=today, expire_date__lte=near_expiry_threshold
        ).values("id", "brand_name", "item_name", "batch_no", "expire_date")

        low_stock = [dict(item) for item in low_stock_qs]
        stock_out = [dict(item) for item in stock_out_qs]
        near_expiry = [
            {**dict(item), "expire_date": item["expire_date"].strftime("%Y-%m-%d")}
            for item in near_expiry_qs
        ]

        stock_alerts = {
            "low_stock": low_stock,
            "stock_out": stock_out,
            "near_expiry": near_expiry,
        }

        # ---------- Weekly Summary ----------
        week_sales = (
            Sale.objects.filter(sale_date__date__gte=last_week)
            .aggregate(total=Sum("total_amount"))
            .get("total") or Decimal("0.00")
        )
        week_transactions = Sale.objects.filter(sale_date__date__gte=last_week).count()

        weekly_summary = {
            "week_sales": float(week_sales),
            "transactions": int(week_transactions),
        }

        # ---------- Inventory Health ----------
        total_products = Medicine.objects.count()
        low_stock_count = len(low_stock)
        near_expiry_count = len(near_expiry)
        stock_out_count = len(stock_out)

        inventory_health = {
            "total_products": int(total_products),
            "low_stock": int(low_stock_count),
            "near_expiry": int(near_expiry_count),
            "stock_out": int(stock_out_count),
        }

        # ---------- Performance Metrics ----------
        # inventory_turnover = total_revenue / inventory_cost (use buying_price as cost base)
        inventory_cost = Medicine.objects.aggregate(
            total_cost=Sum((F("stock_carton") * F("units_per_carton") + F("stock_in_unit")) * F("buying_price"),
                           output_field=DecimalField(max_digits=30, decimal_places=2))
        )["total_cost"] or Decimal("0.00")

        inventory_turnover = (total_revenue / inventory_cost) if (inventory_cost and inventory_cost > 0) else Decimal("0.00")

        performance_metrics = {
            "inventory_turnover": float(inventory_turnover),
        }

        # ---------- Final payload matching the TS interface ----------
        payload = {
            "summary": summary,
            "sales_trend": sales_trend,
            "inventory_by_category": inventory_by_category,
            "top_selling": top_selling,
            "stock_alerts": stock_alerts,
            "weekly_summary": weekly_summary,
            "inventory_health": inventory_health,
            "performance_metrics": performance_metrics,
        }

        return Response(payload)


    # ----------------- PROFIT SUMMARY SECTION -----------------
    @action(detail=False, methods=["get"])
    def profit_summary(self, request):
        today = now().date()
        week_ago = today - timedelta(days=7)
        month_ago = today - timedelta(days=30)

        # Profit from sale items: profit = (price - medicine.buying_price) * quantity
        def calc_profit(qs):
            return qs.annotate(
                profit=(F("price") - F("medicine__buying_price")) * F("quantity")
            ).aggregate(total=Sum("profit")).get("total") or Decimal("0.00")

        daily_profit = calc_profit(SaleItem.objects.filter(sale__sale_date__date=today))
        weekly_profit = calc_profit(SaleItem.objects.filter(sale__sale_date__date__gte=week_ago))
        monthly_profit = calc_profit(SaleItem.objects.filter(sale__sale_date__date__gte=month_ago))

        return Response({
            "daily_profit": float(daily_profit),
            "weekly_profit": float(weekly_profit),
            "monthly_profit": float(monthly_profit),
        })
# -------------------- SETTINGS --------------------
class SettingViewSet(viewsets.ModelViewSet):
    queryset = Setting.objects.all()
    serializer_class = SettingSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        setting, created = Setting.objects.get_or_create(
            defaults={
                "discount": 0.00,
                "low_stock_threshold": 10,
                "expired_date": "09:00:00",
            }
        )
        return setting

    def list(self, request, *args, **kwargs):
        setting = self.get_object()
        serializer = self.get_serializer(setting)
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
        setting = self.get_object()
        serializer = self.get_serializer(setting, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def update(self, request, *args, **kwargs):
        setting = self.get_object()
        serializer = self.get_serializer(setting, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    def destroy(self, request, *args, **kwargs):
        return Response(
            {"detail": "Deleting settings is not allowed."},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )
