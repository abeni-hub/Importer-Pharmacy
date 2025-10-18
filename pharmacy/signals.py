from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import SaleItem

# @receiver(post_save, sender=SaleItem)
# def decrease_stock(sender, instance, created, **kwargs):
#     if created:  # only on new SaleItem
#         medicine = instance.medicine
#         if medicine.stock < instance.quantity:
#             raise ValueError(f"Not enough stock for {medicine.brand_name}")
#         medicine.stock -= instance.quantity
#         medicine.save(update_fields=["stock"])
