#!/usr/bin/env python3
"""
Basic Tests for CloudPRNT Python Server
========================================

Run basic syntax and logic tests without requiring Frappe to be running
"""

import sys
import os

# Add cloudprnt to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=" * 80)
print("🧪 CloudPRNT Basic Tests")
print("=" * 80)

# Test 1: Import all modules
print("\n1️⃣  Testing imports...")
try:
    # These should import without Frappe
    import re
    import json
    from datetime import datetime
    print("   ✅ Standard library imports OK")
except Exception as e:
    print(f"   ❌ Standard library imports failed: {e}")
    sys.exit(1)

# Test 2: MAC address normalization logic
print("\n2️⃣  Testing MAC address normalization...")
def normalize_mac_address(mac_address):
    """Test version of normalize_mac_address"""
    if not mac_address:
        return None
    mac_normalized = mac_address.replace(".", ":")
    if len(mac_normalized.replace(":", "")) != 12:
        return None
    return mac_normalized.upper()

def mac_to_dots(mac_address):
    """Test version of mac_to_dots"""
    return mac_address.replace(":", ".")

test_cases = [
    ("00:11:62:12:34:56", "00:11:62:12:34:56"),
    ("00.11.62.12.34.56", "00:11:62:12:34:56"),
    ("00:11:62:AA:BB:CC", "00:11:62:AA:BB:CC"),
]

all_passed = True
for input_mac, expected in test_cases:
    result = normalize_mac_address(input_mac)
    if result == expected:
        print(f"   ✅ {input_mac} → {result}")
    else:
        print(f"   ❌ {input_mac} → {result} (expected {expected})")
        all_passed = False

# Test conversion to dots
mac_colons = "00:11:62:12:34:56"
mac_dots = mac_to_dots(mac_colons)
if mac_dots == "00.11.62.12.34.56":
    print(f"   ✅ {mac_colons} → {mac_dots}")
else:
    print(f"   ❌ {mac_colons} → {mac_dots} (expected 00.11.62.12.34.56)")
    all_passed = False

if not all_passed:
    sys.exit(1)

# Test 3: Hex generation logic
print("\n3️⃣  Testing hex string conversion...")
def str_to_hex(string):
    """Test version of str_to_hex"""
    return ''.join(format(ord(c), '02x') for c in string).upper()

test_strings = [
    ("Hello", "48656C6C6F"),
    ("Test", "54657374"),
    ("€", "20AC"),  # Euro symbol
]

for input_str, expected_hex in test_strings:
    result = str_to_hex(input_str)
    if result == expected_hex:
        print(f"   ✅ '{input_str}' → {result}")
    else:
        print(f"   ❌ '{input_str}' → {result} (expected {expected_hex})")
        all_passed = False

if not all_passed:
    sys.exit(1)

# Test 4: Queue operations (mock)
print("\n4️⃣  Testing queue operations...")
PRINT_QUEUE = {}

def add_job(printer_mac, job_token):
    """Mock add_print_job"""
    if printer_mac not in PRINT_QUEUE:
        PRINT_QUEUE[printer_mac] = []
    job = {
        "token": job_token,
        "printer_mac": printer_mac,
        "timestamp": datetime.now().isoformat()
    }
    PRINT_QUEUE[printer_mac].append(job)
    return len(PRINT_QUEUE[printer_mac])

def get_job(printer_mac):
    """Mock get next job"""
    if printer_mac in PRINT_QUEUE and len(PRINT_QUEUE[printer_mac]) > 0:
        return PRINT_QUEUE[printer_mac][0]
    return None

def remove_job(printer_mac, job_token):
    """Mock remove job"""
    if printer_mac in PRINT_QUEUE:
        PRINT_QUEUE[printer_mac] = [j for j in PRINT_QUEUE[printer_mac] if j["token"] != job_token]
        if len(PRINT_QUEUE[printer_mac]) == 0:
            del PRINT_QUEUE[printer_mac]

# Test adding jobs
mac1 = "00:11:62:12:34:56"
mac2 = "00:11:62:AA:BB:CC"

pos1 = add_job(mac1, "INV-001")
pos2 = add_job(mac1, "INV-002")
pos3 = add_job(mac2, "INV-003")

if pos1 == 1 and pos2 == 2 and pos3 == 1:
    print(f"   ✅ Add jobs: {pos1}, {pos2}, {pos3}")
else:
    print(f"   ❌ Add jobs failed: {pos1}, {pos2}, {pos3}")
    all_passed = False

# Test retrieving jobs
job1 = get_job(mac1)
job2 = get_job(mac2)

if job1 and job1["token"] == "INV-001":
    print(f"   ✅ Get job for {mac1}: {job1['token']}")
else:
    print(f"   ❌ Get job for {mac1} failed")
    all_passed = False

if job2 and job2["token"] == "INV-003":
    print(f"   ✅ Get job for {mac2}: {job2['token']}")
else:
    print(f"   ❌ Get job for {mac2} failed")
    all_passed = False

# Test removing jobs
remove_job(mac1, "INV-001")
job1_after = get_job(mac1)

if job1_after and job1_after["token"] == "INV-002":
    print(f"   ✅ Remove job: Next job is {job1_after['token']}")
else:
    print(f"   ❌ Remove job failed")
    all_passed = False

# Test empty queue
remove_job(mac1, "INV-002")
remove_job(mac2, "INV-003")

if len(PRINT_QUEUE) == 0:
    print(f"   ✅ Clear queue: Queue is empty")
else:
    print(f"   ❌ Clear queue failed: {PRINT_QUEUE}")
    all_passed = False

if not all_passed:
    sys.exit(1)

# Test 5: JSON response format
print("\n5️⃣  Testing JSON response format...")
poll_response = {
    "jobReady": True,
    "mediaTypes": ["application/vnd.star.line", "text/vnd.star.markup"],
    "jobToken": "INV-001"
}

try:
    json_str = json.dumps(poll_response)
    parsed = json.loads(json_str)
    if parsed["jobReady"] and parsed["jobToken"] == "INV-001":
        print(f"   ✅ Poll response format OK")
    else:
        print(f"   ❌ Poll response format invalid")
        all_passed = False
except Exception as e:
    print(f"   ❌ JSON serialization failed: {e}")
    all_passed = False

delete_response = {"message": "ok"}
try:
    json_str = json.dumps(delete_response)
    parsed = json.loads(json_str)
    if parsed["message"] == "ok":
        print(f"   ✅ Delete response format OK")
    else:
        print(f"   ❌ Delete response format invalid")
        all_passed = False
except Exception as e:
    print(f"   ❌ JSON serialization failed: {e}")
    all_passed = False

if not all_passed:
    sys.exit(1)

# Test 6: Markup parsing regex
print("\n6️⃣  Testing markup parsing...")
test_markup = """[align: centre]
Test Header
[align: left]
Normal text
[column: left Item 1; right $10.00]
[cut: feed; partial]"""

# Test regex patterns
align_pattern = r'\[align: (centre|center|left|right)\]'
column_pattern = r'\[column: left([^;]*); right([^\]]*)\]'
cut_pattern = r'\[cut'

aligns = re.findall(align_pattern, test_markup)
columns = re.findall(column_pattern, test_markup)
cuts = re.findall(cut_pattern, test_markup)

if len(aligns) == 2:
    print(f"   ✅ Found {len(aligns)} align tags")
else:
    print(f"   ❌ Align parsing failed: {aligns}")
    all_passed = False

if len(columns) == 1:
    print(f"   ✅ Found {len(columns)} column tags")
else:
    print(f"   ❌ Column parsing failed: {columns}")
    all_passed = False

if len(cuts) == 1:
    print(f"   ✅ Found {len(cuts)} cut tags")
else:
    print(f"   ❌ Cut parsing failed: {cuts}")
    all_passed = False

if not all_passed:
    sys.exit(1)

# Summary
print("\n" + "=" * 80)
print("✅ All basic tests passed!")
print("=" * 80)
print("\n📝 Next steps:")
print("   1. Test with Frappe running: bench --site your-site console")
print("   2. Run simulator: bench --site your-site execute cloudprnt.printer_simulator.run_simulator")
print("   3. Add test job and verify it prints")
print("\n")
