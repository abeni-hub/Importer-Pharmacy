from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from .views import RegisterView , UserListCreateView, UserDetailView, UserMeView , CustomTokenObtainPairView


urlpatterns = [
    path('token/', CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('register/', RegisterView.as_view(), name='register'),
    path("users/", UserListCreateView.as_view(), name="user-list-create"),   # GET, POST (admin only)
    path('users/<uuid:pk>/', UserDetailView.as_view(), name='user-detail'),   # GET, PUT, PATCH, DELETE (admin only)
    path("users/me/", UserMeView.as_view(), name="user-me"),   # GET, PATCH (self only)
]
