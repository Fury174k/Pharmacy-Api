from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('App', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='barcode',
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text='Barcode / UPC / product code',
                max_length=100,
                null=True,
                unique=True,
            ),
        ),

        migrations.AddField(
            model_name='sale',
            name='business_date',
            field=models.DateField(
                blank=True,
                db_index=True,
                null=True,
                help_text='Calendar date of the business day this sale belongs to.',
            ),
        ),
    ]
