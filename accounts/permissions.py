from rest_framework.permissions import BasePermission

class IsAdmin(BasePermission):
    """
    Allow access only to admin users.
    """

    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated and request.user.role == "admin"

# class IsPharmacist(BasePermission):
#     """
#     Allow access only to pharmacist users.
#     """

#     def has_permission(self, request, view):
#         return request.user and request.user.is_authenticated and request.user.role == "pharmacist"