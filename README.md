# CloudPRNT for Frappe

**Version:** 2.0 (Pure Python) 🎉
**Status:** ✅ Production Ready

A complete CloudPRNT implementation for Frappe Framework, compatible with Star Micronics printers (mC-Print3, TSP650II, etc.).

## 🚀 Features

- ✅ **100% Python** - No PHP required
- ✅ **HTTP Polling** - Standard CloudPRNT protocol
- ✅ **MQTT Push** - Optional push notifications
- ✅ **Multiple Printers** - Manage unlimited printers
- ✅ **Queue System** - In-memory job queue (Redis-ready)
- ✅ **Star Line Mode** - Native binary format support
- ✅ **Star Markup** - Text-based markup support
- ✅ **Auto Status** - Real-time printer status tracking
- ✅ **Print Logs** - Complete print history

## 📋 Prerequisites

- Frappe Framework (v13+)
- Python 3.8+
- Star Micronics CloudPRNT-compatible printer
- **Optional:** MQTT broker (for push notifications)

## 🔧 Installation

### 1. Install App

```bash
cd ~/frappe-bench/apps
bench get-app https://github.com/bvisible/CloudPRNT.git
bench --site your-site install-app cloudprnt
```

### 2. Install Dependencies

```bash
cd ~/frappe-bench
./env/bin/pip install fastapi uvicorn
```

### 3. Run Migration

```bash
bench --site your-site migrate
```

### 4. Setup Standalone Server

CloudPRNT uses a standalone FastAPI server on port 8001 to avoid saturating Frappe's Gunicorn workers.

#### A. Create Nginx Configuration

Add this location block to your Nginx config (e.g., `/etc/nginx/conf.d/frappe-bench.conf`):

```nginx
# CloudPRNT Standalone Server - Python FastAPI on port 8001
location /cloudprnt/ {
    proxy_http_version 1.1;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header Host $host;
    proxy_read_timeout 120;
    proxy_redirect off;

    # Strip /cloudprnt prefix and pass to standalone server
    rewrite ^/cloudprnt/(.*) /$1 break;
    proxy_pass http://127.0.0.1:8001;
}
```

Then reload Nginx:
```bash
sudo nginx -t
sudo systemctl reload nginx
```

#### B. Start Standalone Server

```bash
# Manual start (for testing)
bench --site your-site run-cloudprnt-server

# Or start with nohup for background
cd ~/frappe-bench
nohup ./env/bin/python apps/cloudprnt/cloudprnt/cloudprnt_standalone_server.py > /tmp/cloudprnt-server.log 2>&1 &
```

#### C. Setup Auto-Start with Supervisor (Recommended)

Create `/etc/supervisor/conf.d/cloudprnt-server.conf`:

```ini
[program:cloudprnt-server]
command=/home/frappe/frappe-bench/env/bin/python /home/frappe/frappe-bench/apps/cloudprnt/cloudprnt/cloudprnt_standalone_server.py
directory=/home/frappe/frappe-bench
user=frappe
autostart=true
autorestart=true
redirect_stderr=true
stdout_logfile=/var/log/cloudprnt-server.log
stderr_logfile=/var/log/cloudprnt-server-error.log
```

Then:
```bash
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start cloudprnt-server
```

### 5. Configure Printers

1. Go to **CloudPRNT Settings**
2. Add your printers in the **Printers** table:
   - **Label**: Friendly name (e.g., "Kitchen Printer")
   - **MAC Address**: XX:XX:XX:XX:XX:XX format
   - **Use MQTT**: Check if using MQTT (optional)
3. Set **Default Printer**
4. Save

### 6. Configure Printer Device

In your Star printer's web interface, set CloudPRNT URL to:

```
https://your-domain.tld/cloudprnt/poll
```

**Poll interval:** 5 seconds (recommended)

### 7. Verify Installation

```bash
# Check if standalone server is running
curl http://localhost:8001/health
# Should return: {"status":"ok","timestamp":"...","queued_jobs":0}

# Check external access
curl https://your-domain.tld/cloudprnt/health
# Should return same response

# Monitor printer connections
tail -f /tmp/cloudprnt-server.log
# You should see printer poll requests
```

## 🎯 Quick Start

### Print a POS Invoice

```python
# Via API
import frappe
from cloudprnt.api import print_pos_invoice

# HTTP mode (default)
result = print_pos_invoice("POS-INV-00001")
print(result)
# Output: {"success": True, "method": "http", "queue_position": 1}

# MQTT mode (if configured)
result = print_pos_invoice("POS-INV-00001", use_mqtt=True)
print(result)
# Output: {"success": True, "method": "mqtt"}
```

### Add Job to Queue Directly

```python
from cloudprnt.cloudprnt_server import add_print_job

result = add_print_job("POS-INV-00001", "00:11:62:12:34:56")
print(result)
# Output: {"success": True, "job_token": "POS-INV-00001", ...}
```

### Check Queue Status

```python
from cloudprnt.cloudprnt_server import get_queue_status

# All printers
status = get_queue_status()
print(status)

# Specific printer
status = get_queue_status("00:11:62:12:34:56")
print(status)
# Output: {"printer_mac": "...", "jobs": [...]}
```

## 🧪 Testing

### Run Printer Simulator

```bash
# Start Frappe server
bench --site your-site serve

# In another terminal, start simulator
bench --site your-site execute cloudprnt.printer_simulator.run_simulator
```

The simulator will poll the server and print any jobs to `./cloudprnt_output/`

### Manual Testing

```python
# In Frappe console
from cloudprnt.cloudprnt_server import add_print_job

# Add test job
add_print_job("POS-INV-00001", "00:11:62:12:34:56")

# Simulator should fetch and "print" it within 5 seconds
```

## 📡 MQTT Configuration (Optional)

For push notifications instead of polling:

### 1. Install MQTT Client

```bash
cd frappe-bench
source env/bin/activate
pip install paho-mqtt
```

### 2. Configure Broker

Edit `sites/your-site/site_config.json`:

```json
{
  "mqtt_broker_host": "localhost",
  "mqtt_broker_port": 1883,
  "mqtt_username": "cloudprnt",
  "mqtt_password": "your_password"
}
```

### 3. Enable on Printer

In **CloudPRNT Settings**, check **Use MQTT** for each printer that should use push mode.

### 4. Configure Printer Device

In printer's web interface:
- Enable **MQTT**
- Set broker host, port, credentials
- Set topics to CloudPRNT standard (`star/cloudprnt/...`)

## 📚 Documentation

- **[MIGRATION.md](MIGRATION.md)** - Complete migration guide (PHP → Python)
- **[TESTING.md](TESTING.md)** - Comprehensive testing guide
- **[docs.txt](docs.txt)** - CloudPRNT protocol reference

## 🏗️ Architecture

```
┌─────────────┐
│   Frappe    │
│  POS/ERPNext│
└──────┬──────┘
       │
       ▼
┌──────────────────┐
│  api.py          │  print_pos_invoice()
│  print_job.py    │  StarCloudPRNTStarLineModeJob
│  pos_invoice_... │  get_pos_invoice_markup()
└──────┬───────────┘
       │
       ▼
┌──────────────────────────────┐
│  cloudprnt_server.py         │
│  ├─ Poll endpoint (POST)     │
│  ├─ Job endpoint (GET)       │
│  ├─ Delete endpoint (DELETE) │
│  └─ In-memory queue          │
└──────┬───────────────────────┘
       │
       ▼
┌──────────────┐     ┌──────────────┐
│ HTTP Polling │ OR  │ MQTT Bridge  │
└──────┬───────┘     └──────┬───────┘
       │                    │
       ▼                    ▼
┌────────────────────────────────┐
│   Star mC-Print3 Printer       │
└────────────────────────────────┘
```

## 🔍 Troubleshooting

### Printer not receiving jobs

1. **Check queue:**
   ```python
   from cloudprnt.cloudprnt_server import get_queue_status
   print(get_queue_status())
   ```

2. **Check printer configuration:**
   - Verify CloudPRNT URL is correct
   - Check poll interval (5s recommended)
   - Test network connectivity

3. **Check logs:**
   ```bash
   tail -f sites/your-site/logs/web.log | grep cloudprnt
   ```

### MQTT not working

```python
from cloudprnt.mqtt_bridge import test_mqtt_connection
result = test_mqtt_connection()
print(result)
# Should show: {"success": True, "connected": True}
```

### Clear stuck queue

```python
from cloudprnt.cloudprnt_server import clear_queue
clear_queue()  # Clear all
# or
clear_queue("00:11:62:12:34:56")  # Clear specific printer
```

## 🤝 Contributing

Contributions welcome! Please:

1. Fork the repo
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## 📝 API Reference

### HTTP Endpoints

All endpoints are whitelisted (allow_guest=True):

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/method/cloudprnt.cloudprnt_server.cloudprnt_poll` | Printer polls for jobs |
| GET | `/api/method/cloudprnt.cloudprnt_server.cloudprnt_job` | Fetch job data |
| DELETE | `/api/method/cloudprnt.cloudprnt_server.cloudprnt_delete` | Confirm print |

### Python API

```python
# Print invoice
from cloudprnt.api import print_pos_invoice
print_pos_invoice(invoice_name, printer=None, use_mqtt=False)

# Add job to queue
from cloudprnt.cloudprnt_server import add_print_job
add_print_job(invoice_name, printer_mac=None)

# Queue management
from cloudprnt.cloudprnt_server import get_queue_status, clear_queue
get_queue_status(printer_mac=None)
clear_queue(printer_mac=None)
```

## 📊 Version History

- **v2.0** (2025-11-11) - Pure Python implementation, MQTT support
- **v1.0** (2024-07-09) - Initial PHP-based implementation

## 📄 License

MIT License - See [license.txt](license.txt)

---

**Developed by:** bvisible
**Migrated to Python by:** Claude AI
**Supports:** Star Micronics CloudPRNT Protocol

## License

MIT