from django.db import models
from django.contrib.auth.models import AbstractUser

# Create your models here.
class User(AbstractUser):
    pharmacy_name = models.CharField(max_length=150, blank=True, null=True)
    license_number = models.CharField(max_length=100, blank=True, null=True)

    def __str__(self):
        return self.username