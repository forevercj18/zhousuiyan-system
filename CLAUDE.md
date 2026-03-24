# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Baby birthday party props rental ERP system (宝宝周岁宴道具租赁管理系统). A Django monolith managing orders, transfers, per-unit inventory, BOM/assembly, maintenance/disposal, procurement, finance, approvals, risk events, and audit logs.

**Tech stack:** Django 4.2.9, Django REST Framework, server-rendered templates + vanilla JS, SQLite (dev) / PostgreSQL (prod), WhiteNoise for static files, Waitress (Windows) / Gunicorn (Linux) for WSGI.

## Common Commands

```bash
# Development
python manage.py runserver                    # Start dev server
python manage.py migrate                      # Run database migrations
python manage.py collectstatic --noinput      # Collect static files
python manage.py check                        # System check
python manage.py check --deploy               # Production readiness check

# Testing (Django unittest-based, all in apps/core/tests.py)
python manage.py test apps.core               # Run all tests
python manage.py test apps.core.tests.CoreServicesTestCase  # Single test class
python manage.py test apps.core.tests.CoreServicesTestCase.test_create_order_success  # Single test method

# Management commands
python manage.py check_consistency            # Data consistency check
python manage.py repair_consistency           # Repair data inconsistencies
python manage.py ops_watchdog                 # Operations monitoring
python manage.py approval_sla_remind          # Approval SLA reminders
python manage.py smoke_flow                   # Smoke test flow

# Windows one-click
.\start.bat                                   # Dev startup
.\start_prod_windows.bat                      # Prod startup (Waitress)
```

Settings are selected via `DJANGO_SETTINGS_MODULE`: `config.settings_dev` (dev) or `config.settings_prod` (prod). Database engine is controlled by `DB_ENGINE` env var (`sqlite` or `postgres`).

## Architecture

### Django Apps
- **`apps/core`** — All business logic: models, views, services, permissions, middleware, template tags, management commands, and tests.
- **`apps/api`** — REST API layer (serializers, views, urls) built on DRF, serving a subset of core functionality.

### Layered Design
Business logic lives in **service modules** (`apps/core/services/`), not in views or templates:
- `order_service.py` — Order lifecycle and status transitions
- `inventory_unit_service.py` — Per-unit (单套) inventory tracking
- `assembly_service.py` — BOM assembly, parts consumption, unit creation
- `procurement_service.py` — Purchase orders and parts inventory
- `approval_service.py` — Approval workflow
- `audit_service.py` — Structured audit logging (before/after JSON diffs)
- `notification_service.py` — Internal notifications
- `risk_event_service.py` — Risk event management

**Always route business rules through these services.** Do not scatter logic into views or templates.

### Key Domain Models (`apps/core/models.py`)
Core entity chain: `SKU` → `SKUComponent` (BOM) → `Part` (component inventory) → `AssemblyOrder` → `InventoryUnit` (per-unit tracking) → `Order`/`Transfer`

Other important models: `Reservation`, `PurchaseOrder`, `MaintenanceWorkOrder`, `UnitDisposalOrder`, `PartRecoveryInspection`, `FinanceTransaction`, `ApprovalTask`, `RiskEvent`, `AuditLog`, `SystemSettings`, `PermissionTemplate`.

### Permission System
RBAC defined in `apps/core/permissions.py` with roles: admin, manager, warehouse_manager, warehouse_staff, customer_service. Users can operate in "fixed role" or "custom permissions" mode (`permission_mode` field). Custom permissions support per-module, per-CRUD-action, and per-business-action granularity.

### Configuration
- `config/settings_common.py` — Shared settings (all environments inherit)
- `config/settings_dev.py` — Development overrides
- `config/settings_prod.py` — Production overrides (HTTPS, PostgreSQL, security)

### Templates & Frontend
Server-rendered Django templates in `templates/`. Vanilla JS in `static/js/`. No npm/webpack/frontend build tools. Many pages use inline `<script>` blocks with modal/form interactions.

### State Machines
Strict state machines govern: order status, transfer task status, inventory unit node status, assembly/maintenance/disposal status. Before modifying any state transition, consult the status matrix documents in `docs/`:
- `ORDER_STATUS_MATRIX_20260311.md`
- `TRANSFER_STATUS_MATRIX_20260311.md`
- `INVENTORY_UNIT_NODE_MATRIX_20260311.md`
- `ORDER_TRANSFER_UNIT_LINKAGE_OVERVIEW_20260311.md`

## Development Rules (from RULES_AI.md)

These constraints are critical and must be followed:

1. **Minimal modification principle** — Fix locally, don't refactor broadly. If 1 file suffices, don't touch 10.
2. **Never break business closedloops** — Order, transfer, per-unit inventory, parts inventory, assembly/maintenance/disposal/recovery-inspection flows must remain intact.
3. **State transitions are strict** — Never relax state machine rules. Frontend buttons must match backend allowed transitions. Backend must reject illegal transitions even if frontend is bypassed.
4. **Inventory accuracy is sacred** — SKU inventory, per-unit inventory, parts inventory, and transfer reservations must maintain consistent counts. Never sacrifice accuracy for display convenience.
5. **Reuse existing services** — Route all business logic through `apps/core/services/`. Don't put rules in views or templates.
6. **Don't delete old logic** unless confirmed: no references, replacement exists, no impact on historical data.
7. **Don't swap the tech stack** — No framework replacement, no SPA conversion, no unnecessary new infrastructure.
8. **After any change**: run `python manage.py check` and relevant tests. If modifying status flows, update the corresponding docs.
9. **Document changes**: state what changed, why, which problem category, and whether other modules are affected.
