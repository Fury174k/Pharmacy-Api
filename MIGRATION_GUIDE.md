# Database Migration Guide

## Overview
This document provides step-by-step instructions for applying the offline-first sales sync database changes to your Django backend.

---

## Pre-Migration Checklist

Before running migrations:

- [ ] Backup your production database
- [ ] Test migrations in a staging environment first
- [ ] Stop any running background tasks or workers
- [ ] Ensure no other developers are making migrations simultaneously
- [ ] Commit all code changes to version control

---

## Step 1: Generate Migration

```bash
cd c:\Users\ADMIN\Desktop\WorkFolder\MedDigitalAssistant\Backend

# Create migration file for model changes
python manage.py makemigrations
```

**Expected output:**
```
Migrations for 'App':
  App/migrations/NNNN_auto_YYYYMMDD_HHMM.py
    - Add field external_id to sale
    - Add field source_device to sale
    - Add field client_timestamp to sale
    - Add field synced_at to sale
    - Add field updated_at to sale
```

If you see this, you're good. If you get an error, check:
- Are you in the correct directory?
- Does `manage.py` exist?
- Is Django installed?

---

## Step 2: Review Migration File (Optional but Recommended)

```bash
# View the generated migration
cat App/migrations/NNNN_auto_YYYYMMDD_HHMM.py
```

Should look like:
```python
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [
        ('App', 'MMMM_previous_migration'),
    ]

    operations = [
        migrations.AddField(
            model_name='sale',
            name='external_id',
            field=models.UUIDField(blank=True, null=True, unique=True, ...),
        ),
        migrations.AddField(
            model_name='sale',
            name='source_device',
            field=models.CharField(default='web', max_length=128),
        ),
        migrations.AddField(
            model_name='sale',
            name='client_timestamp',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='sale',
            name='synced_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='sale',
            name='updated_at',
            field=models.DateTimeField(auto_now=True),
        ),
    ]
```

---

## Step 3: Apply Migration

```bash
# Apply the migration to your database
python manage.py migrate
```

**Expected output:**
```
Operations to perform:
  Apply all migrations: App, accounts, ...
Running migrations:
  Applying App.NNNN_auto_YYYYMMDD_HHMM... OK
```

If you see `OK`, the migration succeeded!

### Troubleshooting Migration Failures

**Error: "no such table"**
```bash
# Ensure your database is set up
python manage.py migrate --run-syncdb
```

**Error: "unique constraint already exists"**
- Existing data may conflict with new unique fields
- Since `external_id` has `null=True, blank=True`, this shouldn't happen
- If it does, restore from backup and retry

**Error: "relation already exists"**
- You may have run migrate twice
- Check with: `python manage.py showmigrations`
- If needed, rollback: `python manage.py migrate App <previous_migration_number>`

---

## Step 4: Verify Migration

```bash
# Check all migrations were applied
python manage.py showmigrations
```

Should show a checkmark (✓) next to the new migration:
```
[✓] 0001_initial
[✓] 0002_user_product_...
[✓] ...
[✓] NNNN_auto_YYYYMMDD_HHMM
```

---

## Step 5: Test the Setup

```bash
# Run Django shell to verify model changes
python manage.py shell
```

```python
from App.models import Sale

# Check the new fields exist
sale = Sale._meta.get_fields()
field_names = [f.name for f in sale]

print('external_id' in field_names)      # Should be True
print('source_device' in field_names)    # Should be True
print('client_timestamp' in field_names) # Should be True
print('synced_at' in field_names)        # Should be True
print('updated_at' in field_names)       # Should be True

# Test creating a sale with new fields
from django.contrib.auth import get_user_model
from django.utils import timezone
import uuid

User = get_user_model()
user = User.objects.first()

if user:
    sale = Sale.objects.create(
        sold_by=user,
        external_id=uuid.uuid4(),
        source_device='test-device',
        client_timestamp=timezone.now(),
        total_amount=10.00
    )
    print(f"✓ Sale created: {sale.id}")
    print(f"✓ external_id: {sale.external_id}")
    print(f"✓ source_device: {sale.source_device}")
else:
    print("No users found. Create a user first.")

# Exit shell
exit()
```

---

## Step 6: Run Django Tests (Optional)

```bash
# Run any existing tests to ensure nothing broke
python manage.py test
```

If tests exist and pass, you're all set!

---

## Step 7: Verify API Endpoints

Start the development server:

```bash
python manage.py runserver
```

Test the new offline-sync endpoint:

```bash
# In another terminal, test POST /api/sales/
curl -X POST http://localhost:8000/api/sales/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Token YOUR_AUTH_TOKEN" \
  -d '{
    "items": [
      {
        "product": 1,
        "quantity": "1",
        "unit_price": "10.00"
      }
    ],
    "external_id": "550e8400-e29b-41d4-a716-446655440000",
    "source_device": "test-device",
    "client_timestamp": "2026-01-31T14:30:00Z"
  }'
```

Should return 201 Created with the sale details including the new fields.

Test the analytics endpoint:

```bash
curl -X GET "http://localhost:8000/api/sales/analytics/?product_id=1&start_date=2026-01-01&end_date=2026-01-31" \
  -H "Authorization: Token YOUR_AUTH_TOKEN"
```

Should return aggregated sales data for the product.

---

## Step 8: Deploy to Production (If Applicable)

### For Traditional Server Deployment

```bash
# SSH into production server
ssh user@production-server

# Pull latest code
cd /path/to/MedDigitalAssistant/Backend
git pull origin main

# Create migration backup
python manage.py dumpdata > backup_before_migration.json

# Apply migration
python manage.py migrate

# Collect static files (if needed)
python manage.py collectstatic --noinput

# Restart gunicorn/uwsgi
sudo systemctl restart gunicorn
# or
sudo systemctl restart uwsgi
```

### For Docker Deployment

```bash
# In docker-compose.yml, add migration step
services:
  web:
    build: .
    command: >
      sh -c "python manage.py migrate &&
             python manage.py runserver 0.0.0.0:8000"
    ...

# Deploy
docker-compose up -d
```

### For Cloud (Heroku, AWS, etc.)

```bash
# Heroku example
heroku config:set DEBUG=False
git push heroku main
# Migrations run automatically before release phase
```

---

## Rollback Plan (If Something Goes Wrong)

### Rollback Last Migration

```bash
# Rollback to previous migration
python manage.py migrate App <previous_migration_number>

# Check available migrations
python manage.py showmigrations App
```

Example:
```bash
# If new migration is 0008_auto_..., rollback to 0007
python manage.py migrate App 0007
```

### Restore from Backup

```bash
# If database is corrupted, restore from backup
# (Before running migrations, you should have taken a backup)

# PostgreSQL
psql -U username dbname < backup_before_migration.sql

# MySQL
mysql -u username -p dbname < backup_before_migration.sql

# SQLite
cp backup.db db.sqlite3
```

---

## Post-Migration Validation

### Check Database Schema

```bash
# View the actual database schema
python manage.py dbshell
```

```sql
-- PostgreSQL
\d App_sale;

-- MySQL
DESCRIBE App_sale;

-- SQLite
.schema App_sale;
```

Should show the five new columns:
- `external_id` (UUID, unique, null)
- `source_device` (VARCHAR, default='web')
- `client_timestamp` (DATETIME, null)
- `synced_at` (DATETIME, null)
- `updated_at` (DATETIME, auto_now)

---

## Migration Checklist

- [ ] Backup database
- [ ] Test in staging environment
- [ ] Run `makemigrations`
- [ ] Review migration file
- [ ] Run `migrate`
- [ ] Verify with `showmigrations`
- [ ] Test with Django shell
- [ ] Run test suite (if exists)
- [ ] Test API endpoints manually
- [ ] Deploy to production
- [ ] Monitor logs for errors
- [ ] Verify data integrity post-migration

---

## Common Issues & Solutions

| Issue | Solution |
|-------|----------|
| **"No changes detected"** | Models may not have changed. Check if all code changes are saved. |
| **"Circular import"** | Restart the terminal session and try again. |
| **"Database is locked"** | Close other connections to the database and retry. |
| **"Column already exists"** | Migration may have already been applied. Check `showmigrations`. |
| **"Foreign key constraint failed"** | Ensure related objects exist before creating dependents. |

---

## Performance Considerations

The new fields are:
- `external_id`: Unique index (small performance impact, worth it for idempotency)
- `source_device`: Indexed implicitly by CharField (minimal impact)
- `client_timestamp`: DateTimeField (minimal impact)
- `synced_at`: DateTimeField (minimal impact)
- `updated_at`: Auto-updated (minimal impact)

**Estimated migration time:**
- Small database (< 10k sales): ~1 second
- Medium database (10k-1M sales): ~5-30 seconds
- Large database (> 1M sales): May take minutes; consider off-peak deployment

**To check current sale count:**
```python
python manage.py shell
from App.models import Sale
print(Sale.objects.count())
exit()
```

---

## Next Steps After Migration

1. **Update API Documentation** — Document new fields in `/api/sales/` endpoint
2. **Update Frontend** — Implement offline queue with `external_id` support
3. **Monitor Alerts** — Set up monitoring for low-stock alerts
4. **Test Analytics** — Verify `/api/sales/analytics/` returns correct aggregations
5. **Train Users** — Explain new offline sync capability

---

**Migration complete! Your system is now ready for offline-first sales tracking.**
