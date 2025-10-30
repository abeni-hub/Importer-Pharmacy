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
from django.db.models import Value
from django.db.models.functions import Coalesce
from django.db.models import IntegerField
from datetime import timedelta
from decimal import Decimal
from django.db.models import (
    Sum, F, Value, IntegerField, DecimalField, ExpressionWrapper, Q
)
from django.db.models.functions import Coalesce, TruncDate
from django.utils.timezone import now
from django.views.decorators.cache import cache_page
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from pharmacy.models import Medicine, Sale, SaleItem, Department, Setting

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
# ----------------- OVERVIEW SECTION -----------------
class DashboardViewSet(viewsets.ViewSet):

    # ---------------- OVERVIEW SECTION ----------------
    @action(detail=False, methods=["get"])
    @cache_page(60 * 5)
    def overview(self, request):
        today = now().date()
        near_expiry_threshold = today + timedelta(days=30)

        # ---------- STOCK SECTION ----------
        total_medicines = Medicine.objects.count()

        # Inline expression (avoid alias issue)
        low_stock = Medicine.objects.annotate(
            total_stock=F("stock_in_unit") + F("stock_carton") * F("units_per_carton")
        ).filter(total_stock__lte=F("low_stock_threshold"), total_stock__gt=0).count()

        stock_out = Medicine.objects.annotate(
            total_stock=F("stock_in_unit") + F("stock_carton") * F("units_per_carton")
        ).filter(total_stock__lte=0).count()

        expired = Medicine.objects.filter(expire_date__lt=today).count()
        near_expiry = Medicine.objects.filter(
            expire_date__gte=today, expire_date__lte=near_expiry_threshold
        ).count()

        # ---------- SALES SECTION ----------
        today_sales_qty = (
            SaleItem.objects.filter(sale__sale_date__date=today)
            .aggregate(total=Sum("quantity"))
            .get("total") or 0
        )
        total_sales_qty = (
            SaleItem.objects.aggregate(total=Sum("quantity")).get("total") or 0
        )

        revenue_today = (
            Sale.objects.filter(sale_date__date=today)
            .aggregate(revenue=Coalesce(Sum("total_amount"), Value(0)))
            .get("revenue")
        )
        total_revenue = (
            Sale.objects.aggregate(revenue=Coalesce(Sum("total_amount"), Value(0)))
            .get("revenue")
        )

        # ---------- PROFIT SECTION ----------
        today_profit = SaleItem.objects.filter(sale__sale_date__date=today).aggregate(
            profit=Coalesce(Sum((F("price") - F("medicine__buying_price")) * F("quantity")), Value(0))
        )["profit"]

        total_profit = SaleItem.objects.aggregate(
            profit=Coalesce(Sum((F("price") - F("medicine__buying_price")) * F("quantity")), Value(0))
        )["profit"]

        # ---------- TOP SELLING ----------
        top_selling = (
            SaleItem.objects.values("medicine__brand_name")
            .annotate(total_sold=Sum("quantity"))
            .order_by("-total_sold")[:5]
        )

        # ---------- DEPARTMENTS ----------
        departments = (
            Medicine.objects.values("department__name")
            .annotate(
                total=Coalesce(
                    Sum(
                        F("stock_in_unit") + F("stock_carton") * F("units_per_carton")
                    ),
                    Value(0),
                ),
                total_profit=Coalesce(
                    Sum(
                        (F("price") - F("buying_price"))
                        * (F("stock_in_unit") + F("stock_carton") * F("units_per_carton"))
                    ),
                    Value(0),
                ),
            )
            .order_by("-total")
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
                "today_sales_qty": int(today_sales_qty),
                "total_sales_qty": int(total_sales_qty),
                "revenue_today": float(revenue_today),
                "total_revenue": float(total_revenue),
            },
            "profit": {
                "today_profit": float(today_profit),
                "total_profit": float(total_profit),
            },
            "top_selling": list(top_selling),
            "departments": list(departments),
        })

    # ---------------- PROFIT SUMMARY ----------------
    @action(detail=False, methods=["get"])
    @cache_page(60 * 5)
    def profit_summary(self, request):
        today = now().date()
        week_start = today - timedelta(days=7)
        month_start = today.replace(day=1)

        daily_profit = SaleItem.objects.filter(sale__sale_date__date=today).aggregate(
            profit=Coalesce(Sum((F("price") - F("medicine__buying_price")) * F("quantity")), Value(0))
        )["profit"]

        weekly_profit = SaleItem.objects.filter(sale__sale_date__date__gte=week_start).aggregate(
            profit=Coalesce(Sum((F("price") - F("medicine__buying_price")) * F("quantity")), Value(0))
        )["profit"]

        monthly_profit = SaleItem.objects.filter(sale__sale_date__date__gte=month_start).aggregate(
            profit=Coalesce(Sum((F("price") - F("medicine__buying_price")) * F("quantity")), Value(0))
        )["profit"]

        return Response({
            "daily_profit": float(daily_profit),
            "weekly_profit": float(weekly_profit),
            "monthly_profit": float(monthly_profit),
        })

    # ---------------- ANALYTICS SECTION ----------------
    @action(detail=False, methods=["get"])
    @cache_page(60 * 5)
    def analytics(self, request):
        """
        Returns JSON shaped exactly like the provided AnalyticsData interface.
        URL: /pharmacy/dashboard/analytics/
        """

        today = now().date()
        last_week = today - timedelta(days=6)
        near_expiry_threshold = today + timedelta(days=30)

        # ---------- SUMMARY ----------
        total_revenue = Sale.objects.aggregate(total=Sum("total_amount"))["total"] or Decimal("0.00")
        total_transactions = Sale.objects.count()
        avg_order_value = (total_revenue / total_transactions) if total_transactions > 0 else Decimal("0.00")

        inventory_value_agg = Medicine.objects.aggregate(
            inventory_value=Sum(
                (F("stock_carton") * F("units_per_carton") + F("stock_in_unit")) * F("price"),
                output_field=DecimalField(max_digits=30, decimal_places=2),
            )
        )["inventory_value"] or Decimal("0.00")

        summary = {
            "total_revenue": float(total_revenue),
            "total_transactions": int(total_transactions),
            "avg_order_value": float(avg_order_value),
            "inventory_value": float(inventory_value_agg),
        }

        # ---------- SALES TREND ----------
        sales_trend_qs = (
            Sale.objects.filter(sale_date__date__gte=last_week)
            .annotate(day=TruncDate("sale_date"))
            .values("day")
            .annotate(total_sales=Sum("total_amount"))
            .order_by("day")
        )
        sales_trend = [
            {"day": entry["day"].strftime("%Y-%m-%d"), "total_sales": float(entry["total_sales"] or 0)}
            for entry in sales_trend_qs
        ]

        # ---------- INVENTORY BY CATEGORY ----------
        inv_by_cat_qs = (
            Medicine.objects.values("department__name")
            .annotate(
                value=Sum(
                    (F("stock_carton") * F("units_per_carton") + F("stock_in_unit")) * F("price"),
                    output_field=DecimalField(max_digits=30, decimal_places=2),
                ),
                profit=Sum(
                    (F("price") - F("buying_price"))
                    * (F("stock_carton") * F("units_per_carton") + F("stock_in_unit")),
                    output_field=DecimalField(max_digits=30, decimal_places=2),
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

        # ---------- TOP SELLING ----------
        top_selling_qs = (
            SaleItem.objects.values("medicine__brand_name")
            .annotate(total_sold=Sum("quantity"))
            .order_by("-total_sold")[:5]
        )
        top_selling = [
            {"medicine__brand_name": row["medicine__brand_name"] or "Unknown", "total_sold": int(row["total_sold"] or 0)}
            for row in top_selling_qs
        ]

        # ---------- STOCK ALERTS ----------
        low_stock_qs = Medicine.objects.annotate(
            total_units=(F("stock_carton") * F("units_per_carton") + F("stock_in_unit"))
        ).filter(total_units__lte=F("low_stock_threshold"), total_units__gt=0)

        stock_out_qs = Medicine.objects.annotate(
            total_units=(F("stock_carton") * F("units_per_carton") + F("stock_in_unit"))
        ).filter(total_units__lte=0)

        near_expiry_qs = Medicine.objects.filter(
            expire_date__gte=today, expire_date__lte=near_expiry_threshold
        )

        low_stock = list(low_stock_qs.values("id", "brand_name", "item_name", "batch_no", "stock_in_unit"))
        stock_out = list(stock_out_qs.values("id", "brand_name", "item_name", "batch_no", "stock_in_unit"))
        near_expiry = [
            {"id": m["id"], "brand_name": m["brand_name"], "item_name": m["item_name"], "batch_no": m["batch_no"], "expire_date": m["expire_date"].strftime("%Y-%m-%d")}
            for m in near_expiry_qs.values("id", "brand_name", "item_name", "batch_no", "expire_date")
        ]

        stock_alerts = {
            "low_stock": low_stock,
            "stock_out": stock_out,
            "near_expiry": near_expiry,
        }

        # ---------- WEEKLY SUMMARY ----------
        week_sales = Sale.objects.filter(sale_date__date__gte=last_week).aggregate(total=Sum("total_amount"))["total"] or Decimal("0.00")
        week_transactions = Sale.objects.filter(sale_date__date__gte=last_week).count()
        weekly_summary = {"week_sales": float(week_sales), "transactions": int(week_transactions)}

        # ---------- INVENTORY HEALTH ----------
        total_products = Medicine.objects.count()
        inventory_health = {
            "total_products": total_products,
            "low_stock": len(low_stock),
            "near_expiry": len(near_expiry),
            "stock_out": len(stock_out),
        }

        # ---------- PERFORMANCE METRICS ----------
        inventory_cost = Medicine.objects.aggregate(
            total_cost=Sum(
                (F("stock_carton") * F("units_per_carton") + F("stock_in_unit")) * F("buying_price"),
                output_field=DecimalField(max_digits=30, decimal_places=2),
            )
        )["total_cost"] or Decimal("0.00")

        inventory_turnover = (total_revenue / inventory_cost) if inventory_cost > 0 else Decimal("0.00")

        performance_metrics = {"inventory_turnover": float(inventory_turnover)}

        # ---------- FINAL PAYLOAD ----------
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
