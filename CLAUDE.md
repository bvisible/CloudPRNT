# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CloudPRNT is a Frappe Framework app that implements the Star Micronics CloudPRNT protocol for printing POS invoices. The app was recently migrated from PHP to pure Python (v2.0), featuring both HTTP polling and optional MQTT push support.

## Development Commands

### Setup and Installation
```bash
# Install app in Frappe bench
cd ~/frappe-bench/apps
bench get-app https://github.com/bvisible/CloudPRNT.git
bench --site your-site install-app cloudprnt

# Run migrations
bench --site your-site migrate
```

### Testing
```bash
# Run basic tests (no Frappe required)
python test_basic.py

# Start Frappe development server
bench --site your-site serve

# Run printer simulator (in separate terminal)
bench --site your-site execute cloudprnt.printer_simulator.run_simulator

# Frappe console for manual testing
bench --site your-site console
```

### Testing Print Jobs
```python
# In Frappe console
from cloudprnt.cloudprnt_server import add_print_job, get_queue_status, clear_queue
from cloudprnt.api import print_pos_invoice

# Add job to queue
add_print_job("POS-INV-00001", "00:11:62:12:34:56")

# Check queue status
get_queue_status()  # All printers
get_queue_status("00:11:62:12:34:56")  # Specific printer

# Clear queue
clear_queue()  # All printers
clear_queue("00:11:62:12:34:56")  # Specific printer

# Print invoice (HTTP mode)
print_pos_invoice("POS-INV-00001")

# Print invoice (MQTT mode, if configured)
print_pos_invoice("POS-INV-00001", use_mqtt=True)
```

### MQTT Testing
```python
# Test MQTT connection
from cloudprnt.mqtt_bridge import test_mqtt_connection
test_mqtt_connection()
```

## Architecture

### Core Components

**CloudPRNT Server (`cloudprnt_server.py`)**
- Main HTTP server implementing CloudPRNT protocol endpoints
- In-memory job queue (`PRINT_QUEUE` dict) indexed by printer MAC address
- Three whitelisted endpoints:
  - `POST /cloudprnt_poll` - Printer polls for jobs
  - `GET /cloudprnt_job` - Fetch job data
  - `DELETE /cloudprnt_delete` - Confirm print completion

**Print API (`api.py`)**
- `print_pos_invoice()` - Main entry point for printing
- Automatically selects HTTP (queue) or MQTT (push) mode
- Resolves printer by MAC address or label

**Print Job Generator (`print_job.py`)**
- `StarCloudPRNTStarLineModeJob` - Generates Star Line Mode binary format
- Converts markup to hex commands for Star printers
- Handles barcodes, images, alignment, emphasis

**Markup Generator (`pos_invoice_markup.py`)**
- `get_pos_invoice_markup()` - Converts POS Invoice to Star Markup format
- Text-based markup language specific to Star printers

**MQTT Bridge (`mqtt_bridge.py`)** (optional)
- Singleton MQTT client for push notifications
- Publishes to `star/cloudprnt/to-device/{MAC}/print-job`
- Subscribes to `star/cloudprnt/to-server/{MAC}/print-result`

### Data Flow

```
POS Invoice → print_pos_invoice() → [HTTP Queue OR MQTT Bridge]
                                            ↓              ↓
                              cloudprnt_poll()      MQTT Topics
                                            ↓              ↓
                              cloudprnt_job()      Star Printer
                                            ↓
                              Star Printer receives job
                                            ↓
                              cloudprnt_delete()
                                            ↓
                              Job removed from queue
```

### MAC Address Formats

The codebase uses two MAC address formats:
- **Frappe DB / Internal**: Colons (e.g., `00:11:62:12:34:56`)
- **CloudPRNT Protocol**: Dots (e.g., `00.11.62.12.34.56`)

Use `normalize_mac_address()` and `mac_to_dots()` from `cloudprnt_server.py` for conversions.

### Queue System

Jobs are stored in-memory in the `PRINT_QUEUE` dictionary:
```python
PRINT_QUEUE = {
    "00:11:62:12:34:56": [  # Printer MAC (with colons)
        {
            "token": "POS-INV-001",  # Job identifier
            "invoice": "POS-INV-001",
            "printer_mac": "00:11:62:12:34:56",
            "timestamp": "2025-01-01 12:00:00",
            "status": "pending",
            "media_types": ["application/vnd.star.line", "text/vnd.star.markup"]
        }
    ]
}
```

**Note**: Queue is in-memory only. Jobs are lost on server restart. For production with high reliability needs, consider implementing Redis backend.

## Important Conventions

### Translation Functions

All user-facing strings in Python code use Frappe's translation function:
```python
from frappe import _ as translate

# Correct
message = translate("Facture POS {0} non trouvée").format(invoice_name)
frappe.msgprint(_("Record saved successfully"))

# Incorrect - never hardcode user-facing text
message = "Invoice not found"
```

JavaScript files use `__()`:
```javascript
frappe.msgprint(__("Record saved successfully"));
```

### Error Logging

Use `frappe.log_error()` for error logging:
```python
frappe.log_error(
    message=str(exception),
    title="Error in print_pos_invoice"
)
```

For CloudPRNT-specific logging, use the `neolog()` function from `print_job.py`.

### Printer Status Updates

Always update printer status after poll operations:
```python
update_printer_status(
    mac_address,
    status_code="200 OK",
    printing_in_progress=True
)
```

## File Structure

```
cloudprnt/
├── api.py                      # Main API: print_pos_invoice()
├── cloudprnt_server.py         # HTTP server + queue management
├── mqtt_bridge.py              # MQTT bridge (optional)
├── printer_simulator.py        # Testing tool
├── print_job.py                # Star Line Mode job generator
├── pos_invoice_markup.py       # POS Invoice → Star Markup
├── hooks.py                    # Frappe app hooks
├── cloudprnt/
│   └── doctype/
│       ├── cloudprnt_settings/    # Settings DocType
│       ├── cloudprnt_printers/    # Printer configuration
│       └── cloudprnt_logs/        # Print logs
└── public/
    └── js/
        └── pos_invoice.js      # Frontend: Print button
```

## MQTT Configuration

MQTT is optional. When enabled in `site_config.json`:
```json
{
  "mqtt_broker_host": "localhost",
  "mqtt_broker_port": 1883,
  "mqtt_username": "cloudprnt",
  "mqtt_password": "your_password"
}
```

Then enable per-printer in CloudPRNT Settings by checking "Use MQTT" field.

## Star Printer Protocol

### Media Types Supported
1. `application/vnd.star.line` - Binary Star Line Mode (preferred)
2. `text/vnd.star.markup` - Text-based Star Markup

### Star Line Mode Commands
Defined in `StarCloudPRNTStarLineModeJob` class as hex constants:
- `SLM_NEW_LINE_HEX = "0A"`
- `SLM_SET_EMPHASIZED_HEX = "1B45"`
- `SLM_SET_CENTER_ALIGNMENT_HEX = "1B1D6101"`
- etc.

When modifying print output, work with these hex commands or the markup language, not raw binary.

## Migration Notes

This app was migrated from PHP to Python (v2.0). Key changes:
- PHP files in `cloudprnt/cloudprnt/php/` are deprecated but kept for reference
- Queue moved from disk files to in-memory dict
- Print jobs now triggered via Python queue or MQTT instead of PHP file writes
- See MIGRATION.md for full migration details

## Common Pitfalls

1. **MAC Address Format**: Always normalize MAC addresses. Printers send with dots, DB stores with colons.

2. **Queue Persistence**: Queue is in-memory. Server restart clears all jobs.

3. **MQTT Fallback**: If MQTT fails, the code automatically falls back to HTTP queue mode.

4. **Whitelisted Methods**: CloudPRNT endpoints must remain whitelisted (`@frappe.whitelist(allow_guest=True)`) so printers can poll without authentication.

5. **Hex Generation**: Star Line Mode jobs must be uppercase hex strings. Use `.upper()` on all hex output.

## Documentation References

- **README.md** - Installation and quick start
- **MIGRATION.md** - PHP to Python migration details
- **TESTING.md** - Comprehensive testing guide
- **docs.txt** - CloudPRNT protocol reference
