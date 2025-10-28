from rest_framework import viewsets, permissions, filters, status
from django_filters.rest_framework import DjangoFilterBackend, FilterSet, CharFilter
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils import timezone
from django.core.cache import cache
from django.views.decorators.cache import cache_page
from django.utils.decorators import method_decorator
from django.db.models import Q, F, Sum, Count, Avg, FloatField
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
    @method_decorator(cache_page(60 * 5))  # Cache for 5 minutes
    @action(detail=False, methods=["get"], url_path="alerts")
    def alerts(self, request):
        """
        Combined alert endpoint: Returns all medicines that are expired OR low stock.
        Cached for 5 minutes to reduce DB hits.
        """
        today = date.today()
        queryset = Medicine.objects.filter(
            Q(expire_date__lte=today) | Q(stock_in_unit__lte=F("low_stock_threshold"))
        ).only("id", "brand_name", "item_name", "stock_in_unit", "expire_date", "low_stock_threshold")

        serializer = self.get_serializer(queryset, many=True)

        expired_count = queryset.filter(expire_date__lte=today).count()
        low_stock_count = queryset.filter(stock__lte=F("low_stock_threshold")).count()

        return Response(
            {
                "alert": True if queryset.exists() else False,
                "expired_count": expired_count,
                "low_stock_count": low_stock_count,
                "total_alerts": queryset.count(),
                "message": f"{expired_count} expired and {low_stock_count} low-stock medicines found.",
                "data": serializer.data,
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

    # ---------------- CUSTOM STOCK & EXPORT ----------------
    @action(detail=False, methods=["get"], url_path="export-excel")
    def export_excel(self, request):
        queryset = self.get_queryset()
        df = pd.DataFrame(list(queryset.values()))
        if df.empty:
            return Response({"detail": "No medicine records found."}, status=404)

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
    Dashboard API for summary metrics
    """

    @action(detail=False, methods=["get"])
    def overview(self, request):
        today = now().date()
        near_expiry_threshold = today + timedelta(days=30)

        total_medicines = Medicine.objects.count()
        medicines = Medicine.objects.all()

        low_stock = Medicine.objects.filter(stock__lte=10, stock__gt=0).count()
        stock_out = Medicine.objects.filter(stock=0).count()
        expired = Medicine.objects.filter(expire_date__lt=today).count()
        near_expiry = Medicine.objects.filter(
            expire_date__gte=today, expire_date__lte=near_expiry_threshold
        ).count()

        today_sales_qty = (
            SaleItem.objects.filter(sale__sale_date__date=today)
            .aggregate(total=Sum("quantity"))
            .get("total")
            or 0
        )
        total_sales_qty = (
            SaleItem.objects.aggregate(total=Sum("quantity")).get("total") or 0
        )
        revenue_today = (
            Sale.objects.filter(sale_date__date=today)
            .aggregate(revenue=Sum("total_amount"))
            .get("revenue")
            or 0
        )
        total_revenue = (
            Sale.objects.aggregate(revenue=Sum("total_amount")).get("revenue") or 0
        )

        return Response(
            {
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
            }
        )


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
