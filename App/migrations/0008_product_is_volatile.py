# Generated manually to add is_volatile field to Product model
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('App', '0007_alert_alertpreference_lowstockalert'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='is_volatile',
            field=models.BooleanField(default=False, help_text='Whether this product is volatile (price/quantity not persisted to stock)'),
        ),
    ]