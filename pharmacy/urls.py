from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    MedicineViewSet,
    SaleViewSet,
    DepartmentViewSet,
    DashboardViewSet,
    SettingViewSet,
)

router = DefaultRouter()
router.register(r'medicines', MedicineViewSet, basename='medicines')
router.register(r'sales', SaleViewSet, basename='sales')
router.register(r'departments', DepartmentViewSet, basename='departments')
# router.register(r'refills', RefillViewSet, basename='refills')
router.register(r'dashboard', DashboardViewSet, basename='dashboard')
router.register(r'settings', SettingViewSet, basename='settings')

urlpatterns = [
    path('', include(router.urls)),

    # Optional custom routes (if needed later)
    # path('medicines/<int:pk>/variants/', MedicineVariantView.as_view(), name='medicine-variants'),
]