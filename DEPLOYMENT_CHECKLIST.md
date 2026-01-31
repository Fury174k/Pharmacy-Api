# Offline-First Sales Sync: Deployment & Verification Checklist

---

## Pre-Deployment (Development Environment)

### Code Review
- [ ] Review [App/models.py](App/models.py) — Sale model changes
- [ ] Review [App/serializers.py](App/serializers.py) — SaleSerializer rewrite
- [ ] Review [App/views.py](App/views.py) — New endpoints and logic
- [ ] Review [App/urls.py](App/urls.py) — Route additions
- [ ] Verify no syntax errors: `python manage.py check`
- [ ] Run linter/formatter: `black . && flake8 .` (if configured)

### Unit Tests
- [ ] Write/update tests for idempotent external_id logic
- [ ] Write/update tests for volatile product handling
- [ ] Write/update tests for tracked product stock deduction
- [ ] Write/update tests for alert triggering
- [ ] Write/update tests for transaction rollback
- [ ] Write/update tests for analytics endpoint
- [ ] Run all tests: `python manage.py test`
- [ ] Verify test coverage > 80% for modified code

### Local Development
- [ ] Generate migrations: `python manage.py makemigrations`
- [ ] Apply migrations: `python manage.py migrate`
- [ ] Create test user: `python manage.py createsuperuser`
- [ ] Create test products: volatile + tracked
- [ ] Manual API testing with Postman/curl
- [ ] Verify idempotency (POST same external_id twice)
- [ ] Verify stock deduction (tracked products)
- [ ] Verify no stock deduction (volatile products)
- [ ] Verify analytics endpoint returns correct data

### Documentation Review
- [ ] Review [OFFLINE_SYNC_GUIDE.md](OFFLINE_SYNC_GUIDE.md)
- [ ] Review [FRONTEND_INTEGRATION.md](FRONTEND_INTEGRATION.md)
- [ ] Review [IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md)
- [ ] Review [MIGRATION_GUIDE.md](MIGRATION_GUIDE.md)
- [ ] Review [ARCHITECTURE_DIAGRAMS.md](ARCHITECTURE_DIAGRAMS.md)
- [ ] Review [README_OFFLINE_SYNC.md](README_OFFLINE_SYNC.md)
- [ ] Ensure all examples are accurate and tested

---

## Staging Environment Deployment

### Database Backup & Testing
- [ ] Backup production database (if upgrading from prod)
- [ ] Create staging database from production snapshot
- [ ] Test migrations on staging DB
- [ ] Verify migration time acceptable for production
- [ ] Check disk space for migration
- [ ] Verify no data loss after migration

### Code Deployment to Staging
- [ ] Push code to staging branch
- [ ] Deploy via CI/CD pipeline or manual process
- [ ] Verify code deployed correctly
- [ ] Check logs for errors: `tail -f logs/django.log`

### Functionality Verification on Staging
- [ ] Test offline-first sync with idempotency
  ```bash
  curl -X POST http://staging-api.com/api/sales/ \
    -H "Authorization: Token ..." \
    -d '{"items":[...],"external_id":"UUID-1"}'
  
  # Retry with same UUID-1 → should return 200 OK
  ```
- [ ] Test volatile product (no stock deduction)
- [ ] Test tracked product (stock deduction)
- [ ] Test negative stock scenario
- [ ] Test low-stock alert triggering
- [ ] Test product creation on-the-fly
- [ ] Test analytics endpoint with realistic data
- [ ] Test transaction rollback (simulate error)
- [ ] Verify user-scoped queries (no cross-user access)
- [ ] Load test with concurrent requests
- [ ] Test error scenarios (400, 404, 500 responses)

### API Documentation Updates
- [ ] Update API docs with new fields (external_id, source_device, etc.)
- [ ] Add example payloads for new endpoints
- [ ] Document error responses
- [ ] Document pagination (if applicable)
- [ ] Add rate limiting notes (if applicable)

### Team Communication
- [ ] Notify QA of staging deployment
- [ ] Share API documentation with frontend team
- [ ] Schedule frontend integration testing window
- [ ] Prepare rollback procedure documentation

---

## Production Deployment

### Pre-Production Steps
- [ ] Freeze feature branches (no concurrent PRs to main)
- [ ] Create release branch (e.g., `release/offline-sync-v1`)
- [ ] Final code review and approval
- [ ] Final QA sign-off on staging
- [ ] Document rollback procedure
- [ ] Alert on-call team of deployment window
- [ ] Schedule maintenance window (if needed for large datasets)

### Production Backup
- [ ] Full database backup
- [ ] Application code backup/tag in version control
- [ ] Config backup (env vars, settings)
- [ ] Document backup locations and restoration procedure

### Database Migration (Production)
- [ ] Run during low-traffic period (off-peak hours)
- [ ] Monitor migration progress
  ```bash
  python manage.py migrate --verbosity=2
  ```
- [ ] Verify schema changes:
  ```bash
  python manage.py dbshell
  # SELECT * FROM App_sale LIMIT 1;  -- Check new columns
  ```
- [ ] Verify no errors in application logs
- [ ] Confirm no performance degradation

### Application Deployment (Production)
- [ ] Deploy new code to production
- [ ] Run health checks
- [ ] Monitor error logs
- [ ] Monitor application metrics (latency, error rates)
- [ ] Verify all endpoints accessible
- [ ] Check API response times

### Post-Deployment Verification
- [ ] Test idempotent sync (live production data)
- [ ] Test new analytics endpoint
- [ ] Verify existing sales unaffected
- [ ] Monitor database query performance
- [ ] Check alert system functioning
- [ ] Verify user accounts still accessible

### Monitoring Setup
- [ ] Alert on unique constraint violations (external_id duplicates)
- [ ] Alert on transaction rollbacks (errors)
- [ ] Alert on high error rate (500 errors)
- [ ] Monitor query performance (analytics endpoint)
- [ ] Monitor disk space usage (new fields)
- [ ] Set up CloudWatch/DataDog dashboards (if using)

### Frontend Rollout (Coordinated with Backend)
- [ ] Deploy frontend code using new external_id field
- [ ] Test offline queue functionality
- [ ] Test online sync
- [ ] Monitor frontend error logs
- [ ] Verify analytics queries work from frontend

---

## Verification Checklist (Post-Deployment)

### API Endpoints
- [ ] POST /api/sales/ — Create sale with external_id
  - [ ] 201 Created for new sale
  - [ ] 200 OK for duplicate external_id
  - [ ] 400 for validation errors
  - [ ] 404 for non-existent products
- [ ] GET /api/sales/ — List user's sales
  - [ ] Returns only user's sales (user-scoped)
  - [ ] Includes new fields (external_id, source_device, etc.)
- [ ] GET /api/sales/analytics/ — Product sales aggregation
  - [ ] Returns correct totals
  - [ ] Filters by date range
  - [ ] Supports period grouping
  - [ ] Works for volatile products
  - [ ] Works for tracked products

### Data Integrity
- [ ] No duplicate sales created (external_id unique)
- [ ] All SaleItems have correct subtotals
- [ ] All Sales have correct total_amount
- [ ] Stock values consistent (no phantom deductions)
- [ ] Low-stock alerts accurate and not duplicated

### Performance
- [ ] Analytics query < 1 second (small dataset)
- [ ] Sales creation < 500ms
- [ ] No N+1 query issues
- [ ] Database indexes in place (external_id)

### Security
- [ ] Users can only see their own sales
- [ ] Users cannot manipulate stock values
- [ ] Stock is always computed server-side
- [ ] Invalid tokens rejected (401)
- [ ] CSRF protection enabled

### Offline Functionality
- [ ] Frontend can queue sales locally
- [ ] Queued sales sync when online
- [ ] Idempotency prevents duplicates on retry
- [ ] network errors don't lose data

### Alerts & Monitoring
- [ ] Low-stock alerts created for tracked products
- [ ] Alerts only for stock < reorder_level
- [ ] Alerts not duplicated
- [ ] Alert dismissal working

---

## Rollback Procedure (If Issues Arise)

### Immediate Actions (First 15 Minutes)
- [ ] Disable new endpoints (remove from urlpatterns)
- [ ] Revert code to previous version
- [ ] Restart application servers
- [ ] Monitor error rates (should drop)

### Database Rollback (If Needed)
```bash
# Option 1: Rollback to previous migration
python manage.py migrate App <previous_migration_number>

# Option 2: Restore from backup (if schema is corrupted)
# PostgreSQL:
pg_restore --dbname=dbname backup.dump

# MySQL:
mysql dbname < backup.sql

# SQLite:
cp backup.db db.sqlite3
```

### Verification After Rollback
- [ ] Test existing sales still accessible
- [ ] Test existing products still working
- [ ] Test stock deductions still working (old logic)
- [ ] Error rates back to normal
- [ ] No data loss detected

### Post-Incident Review
- [ ] Document what went wrong
- [ ] Root cause analysis
- [ ] Fix identified issues
- [ ] Re-test in staging
- [ ] Schedule re-deployment

---

## Production Monitoring (First 48 Hours)

### Every Hour
- [ ] Check error logs for 500 errors
- [ ] Monitor API response times
- [ ] Verify alerts working
- [ ] Check database size increase

### Daily (First 2 Days)
- [ ] Verify analytics endpoint performance
- [ ] Check for duplicate sales (query: `SELECT COUNT(DISTINCT external_id) FROM Sale;`)
- [ ] Verify stock values consistent
- [ ] Monitor user feedback/support tickets
- [ ] Check backend logs for warnings

### Weekly (First Month)
- [ ] Verify idempotency in action (log duplicate requests)
- [ ] Analyze offline sync rate (% of sales queued vs online)
- [ ] Check analytics accuracy vs manual spot-checks
- [ ] Verify low-stock alerts accuracy
- [ ] Monitor database performance metrics

---

## Continuous Integration / CD Pipeline

If using automated deployment:

```yaml
# .github/workflows/deploy.yml example
on:
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - uses: actions/setup-python@v2
      - name: Install dependencies
        run: pip install -r requirements.txt
      - name: Run linter
        run: black --check . && flake8 .
      - name: Run tests
        run: python manage.py test
      - name: Check migrations
        run: python manage.py makemigrations --check

  deploy-staging:
    needs: test
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/develop'
    steps:
      - name: Deploy to staging
        run: ./scripts/deploy-staging.sh

  deploy-production:
    needs: [test, deploy-staging]
    runs-on: ubuntu-latest
    if: github.ref == 'refs/heads/main'
    steps:
      - name: Backup database
        run: ./scripts/backup-db.sh
      - name: Deploy to production
        run: ./scripts/deploy-prod.sh
      - name: Run migrations
        run: python manage.py migrate
      - name: Health check
        run: curl -f https://api.example.com/health/ || exit 1
```

---

## Success Criteria

✅ All tests passing (unit + integration)  
✅ No errors in production logs (first 24 hours)  
✅ Idempotency working (verified with duplicate requests)  
✅ Stock deductions accurate (spot-check 10 sales)  
✅ Analytics queries correct (compare with manual calcs)  
✅ Low-stock alerts triggering (test with reorder_level)  
✅ Offline queue working (test with network toggle)  
✅ Performance acceptable (analytics < 1 second)  
✅ No security issues (user-scoped queries verified)  
✅ Frontend integration complete (team confirms)  
✅ User feedback positive (support tickets minimal)  
✅ Database size within expected range  

---

## Support & Runbooks

### Common Issues & Fixes

**Issue: "Duplicate external_id" error**
- Cause: Multiple concurrent requests with same UUID
- Fix: Ensure frontend generates unique UUID per sale

**Issue: Stock not deducting for tracked products**
- Cause: Product.is_volatile = true by mistake
- Fix: Verify is_volatile = false for inventory-tracked products

**Issue: Analytics showing 0 results**
- Cause: Wrong date range or product_id
- Fix: Check client_timestamp vs timestamp fields

**Issue: Performance degradation after deployment**
- Cause: Missing database index on external_id
- Fix: Verify index exists: `CREATE UNIQUE INDEX idx_external_id ON Sale(external_id);`

---

## Documentation for Team

### For Backend Developers
- Reference: [OFFLINE_SYNC_GUIDE.md](OFFLINE_SYNC_GUIDE.md)
- Architecture: [ARCHITECTURE_DIAGRAMS.md](ARCHITECTURE_DIAGRAMS.md)
- Implementation: [IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md)

### For Frontend Developers
- Reference: [FRONTEND_INTEGRATION.md](FRONTEND_INTEGRATION.md)
- API Examples: Included in guide with JavaScript/Vue examples

### For DevOps/DBAs
- Migration: [MIGRATION_GUIDE.md](MIGRATION_GUIDE.md)
- This checklist (deployment & verification)

### For Product/Stakeholders
- Overview: [README_OFFLINE_SYNC.md](README_OFFLINE_SYNC.md)
- Features summary: Hard rules, product types, analytics

---

## Final Checklist Before Going Live

### 1. Code
- [ ] All files reviewed and tested
- [ ] No debugging code left in
- [ ] Error handling comprehensive
- [ ] Logging sufficient for debugging

### 2. Database
- [ ] Migrations tested on staging
- [ ] Backup created
- [ ] Rollback procedure documented
- [ ] Performance acceptable

### 3. Tests
- [ ] Unit tests passing
- [ ] Integration tests passing
- [ ] Manual testing completed
- [ ] Load testing passed

### 4. Documentation
- [ ] API docs updated
- [ ] Runbooks written
- [ ] Team trained
- [ ] Support tickets prepared

### 5. Monitoring
- [ ] Alerts configured
- [ ] Dashboards ready
- [ ] On-call team briefed
- [ ] Escalation procedures clear

### 6. Communication
- [ ] Stakeholders informed
- [ ] Frontend team ready
- [ ] Support team trained
- [ ] Users notified (if applicable)

---

**Deployment and verification checklist complete. Ready for production!**
