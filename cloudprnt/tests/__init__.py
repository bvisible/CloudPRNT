"""
CloudPRNT Tests Package
=======================

Test suite for CloudPRNT application.

Run all tests:
    cd ~/frappe-bench
    bench --site sitename run-tests cloudprnt

Run specific test module:
    bench --site sitename run-tests cloudprnt.tests.test_standalone_server
    bench --site sitename run-tests cloudprnt.tests.test_print_queue_manager
    bench --site sitename run-tests cloudprnt.tests.test_print_job
    bench --site sitename run-tests cloudprnt.tests.test_cputil_integration

Run with coverage:
    bench --site sitename run-tests cloudprnt --coverage

Run with pytest directly:
    cd ~/frappe-bench/apps/cloudprnt
    pytest cloudprnt/tests/ -v
"""

__version__ = "2.1.0"
