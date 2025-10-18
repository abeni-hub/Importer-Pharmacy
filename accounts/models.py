from django.contrib.auth.models import AbstractUser
from django.db import models
import uuid

class User(AbstractUser):
    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False
    )
    role = models.CharField(max_length=50, choices=[("admin", "Admin"), ("pharmacist", "Pharmacist")])
    def __str__(self):
        return f"{self.username} ({self.role})"
