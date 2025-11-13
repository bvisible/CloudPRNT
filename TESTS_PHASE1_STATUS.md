# Tests Phase 1 - Status

## ✅ Fichiers Créés (5/9)

### Configuration & Infrastructure
1. ✅ **pytest.ini** - Configuration pytest complète
2. ✅ **conftest.py** (root) - Fixtures globales + auto-skip CPUtil/MQTT
3. ✅ **requirements-test.txt** - Dépendances tests
4. ✅ **cloudprnt/tests/utils.py** - Utilitaires tests (15 fonctions)
5. ✅ **cloudprnt/tests/test_standalone_server.py** - 10 tests FastAPI

### À Créer (4 fichiers restants)

6. **cloudprnt/tests/test_print_queue_manager.py** - 10 tests DB queue
7. **cloudprnt/tests/test_print_job.py** - 12 tests génération (PNG, UTF-8)
8. **cloudprnt/tests/conftest.py** - Fixtures spécifiques tests/
9. **cloudprnt/tests/__init__.py** - Marker package

## 📝 Résumé des Tests Créés

### test_standalone_server.py (10 tests)

#### TestStandaloneServerHealth (2 tests)
- ✅ test_health_endpoint_returns_200
- ✅ test_health_endpoint_format

#### TestStandaloneServerPoll (4 tests)
- ✅ test_poll_no_jobs_returns_empty
- ✅ test_poll_with_job_returns_job_ready
- ✅ test_poll_mac_address_normalization
- ✅ test_poll_invalid_mac_rejected

#### TestStandaloneServerJob (3 tests)
- ✅ test_job_endpoint_returns_hex
- ✅ test_job_endpoint_returns_png_fallback
- ✅ test_job_endpoint_invalid_token

#### TestStandaloneServerDelete (2 tests)
- ✅ test_delete_endpoint_removes_job
- ✅ test_delete_endpoint_invalid_token

#### TestStandaloneServerConcurrency (1 test)
- ✅ test_concurrent_requests_different_printers

## 🎯 Prochaines Étapes

### Option 1: Terminer Phase 1 localement
```bash
# Créer les 4 fichiers restants:
# - test_print_queue_manager.py
# - test_print_job.py
# - conftest.py (tests/)
# - __init__.py

# Total attendu: 32 tests Phase 1
```

### Option 2: Déployer et tester ce qui existe
```bash
# 1. Commit ce qui est créé
git add pytest.ini conftest.py requirements-test.txt
git add cloudprnt/tests/utils.py
git add cloudprnt/tests/test_standalone_server.py
git add cloudprnt/tests/test_cputil_integration.py

git commit -m "test: Add Phase 1 testing infrastructure

- Add pytest.ini configuration
- Add root conftest.py with global fixtures
- Add requirements-test.txt
- Add test utilities (15 helper functions)
- Add test_standalone_server.py (10 tests)
- Keep existing test_cputil_integration.py (15 tests)

Total: 25 tests ready to run"

git push origin main

# 2. Sur develop
cd /home/neoffice/frappe-bench/apps/cloudprnt
git pull

# 3. Installer dépendances test
pip install -r requirements-test.txt

# 4. Run tests
cd /home/neoffice/frappe-bench
bench --site prod.neoffice.me run-tests cloudprnt.tests.test_standalone_server --verbose
bench --site prod.neoffice.me run-tests cloudprnt.tests.test_cputil_integration --verbose
```

## 📊 Couverture Estimée

### Avec les 5 fichiers créés:
- **test_cputil_integration.py**: 15 tests (cputil_wrapper.py)
- **test_standalone_server.py**: 10 tests (cloudprnt_standalone_server.py)
- **Total**: 25 tests
- **Couverture estimée**: ~25% des modules critiques

### Avec Phase 1 complète (9 fichiers):
- **test_cputil_integration.py**: 15 tests
- **test_standalone_server.py**: 10 tests
- **test_print_queue_manager.py**: 10 tests
- **test_print_job.py**: 12 tests
- **Total**: 47 tests
- **Couverture estimée**: ~60% des modules critiques

## 🔧 Templates pour Fichiers Restants

### test_print_queue_manager.py (à créer)
```python
"""Tests for Database Queue Manager"""
import pytest
from cloudprnt.print_queue_manager import (
    add_job_to_queue, get_next_job, mark_job_printed, clear_queue
)

class TestPrintQueueManager:
    # 10 tests:
    # - test_add_job_creates_record
    # - test_get_next_job_returns_oldest
    # - test_mark_job_printed_updates_status
    # - test_clear_queue_removes_all
    # - test_queue_position_calculation
    # - test_concurrent_job_addition
    # - test_queue_isolation_between_printers
    # - test_job_expiration
    # - test_get_queue_status
    # - test_edge_cases_empty_queue
```

### test_print_job.py (à créer)
```python
"""Tests for Print Job Generation"""
import pytest
from cloudprnt.print_job import (
    StarCloudPRNTStarLineModeJob, generate_receipt_png
)

class TestPNGGeneration:
    # 6 tests:
    # - test_generate_receipt_png_basic
    # - test_generate_receipt_png_utf8
    # - test_generate_receipt_png_alignment
    # - test_generate_receipt_png_font_fallback
    # - test_generate_receipt_png_dimensions
    # - test_png_signature_valid

class TestStarLineModeJob:
    # 6 tests:
    # - test_job_init_python_native
    # - test_job_init_cputil_if_available
    # - test_build_job_from_markup
    # - test_str_to_hex_utf8
    # - test_barcode_generation
    # - test_hex_output_format
```

## 💡 Recommandation

**Je recommande Option 2: Déployer maintenant et tester**

Raisons:
1. 25 tests existants (CPUtil + Standalone) sont déjà utiles
2. Valider l'infrastructure test sur develop
3. Identifier problèmes early
4. Continuer Phase 1 après validation

Veux-tu que je:
A) Crée les 4 fichiers restants maintenant
B) Prépare le commit pour déployer ce qui existe
C) Les deux?
