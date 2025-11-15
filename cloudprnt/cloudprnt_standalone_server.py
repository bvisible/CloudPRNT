#!/usr/bin/env python3
"""
CloudPRNT Standalone HTTP Server
=================================

High-performance standalone server for CloudPRNT protocol.
Runs independently of Frappe's Gunicorn workers to avoid saturation.

Port: 8001
Endpoints:
  - POST /poll
  - GET /job?mac=XX.XX.XX.XX.XX.XX
  - DELETE /job?mac=XX.XX.XX.XX.XX.XX

Usage:
  bench --site prod.local run-cloudprnt-server
  or
  cd frappe-bench && python3 apps/cloudprnt/cloudprnt/cloudprnt_standalone_server.py
"""

import os
import sys
import re
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, Request, Response, Query
from fastapi.responses import JSONResponse, PlainTextResponse
import uvicorn

# Add Frappe bench to path if needed
if os.path.exists("/home/neoffice/frappe-bench"):
    bench_path = "/home/neoffice/frappe-bench"
else:
    bench_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))

sys.path.insert(0, bench_path)
# Add cloudprnt app directory so modules can be imported
cloudprnt_path = os.path.join(bench_path, "apps", "cloudprnt", "cloudprnt")
if cloudprnt_path not in sys.path:
    sys.path.insert(0, cloudprnt_path)

os.chdir(bench_path)

# Frappe will be initialized per-request to avoid connection issues
import frappe

app = FastAPI(title="CloudPRNT Standalone Server", version="1.0.0")

# Global variables for queue (will be populated from Redis)
PRINT_QUEUE = {}


def init_frappe():
    """Initialize Frappe connection for this request"""
    if not hasattr(frappe.local, 'site') or not frappe.local.site:
        sites_path = os.path.join(bench_path, "sites")

        # Disable logging during init to avoid issues
        import logging
        old_level = logging.root.level
        logging.disable(logging.CRITICAL)

        try:
            frappe.init(site="prod.local", sites_path=sites_path)
            frappe.connect()
            frappe.set_user("Administrator")
        except Exception as e:
            # Silently handle errors - connection still works for database queries
            pass
        finally:
            logging.disable(old_level)


def normalize_mac_address(mac_address):
    """Normalize MAC address from dots to colons"""
    if not mac_address:
        return None
    mac_normalized = mac_address.replace(".", ":")
    if len(mac_normalized.replace(":", "")) != 12:
        return None
    return mac_normalized.upper()


def mac_to_dots(mac_address):
    """Convert MAC address to dot format"""
    return mac_address.replace(":", ".")


def get_next_job_for_printer(printer_mac):
    """
    Get next job for printer from database queue using raw MySQL

    :param printer_mac: Printer MAC address
    :return: Job dict or None
    """
    try:
        import json
        import pymysql

        # Normalize MAC address to uppercase with colons
        printer_mac_normalized = printer_mac.upper()
        print(f"[CloudPRNT] Looking for jobs for printer MAC: {printer_mac_normalized}")

        # Get database config from Frappe site_config
        import os
        site_config_path = os.path.join(bench_path, "sites", "prod.local", "site_config.json")
        with open(site_config_path, 'r') as f:
            site_config = json.load(f)

        # Connect directly to MySQL
        conn = pymysql.connect(
            host=site_config.get('db_host', 'localhost'),
            user=site_config.get('db_user', site_config.get('db_name', 'root')),
            password=site_config.get('db_password', ''),
            database=site_config.get('db_name'),
            charset='utf8mb4'
        )

        try:
            with conn.cursor(pymysql.cursors.DictCursor) as cursor:
                # Use UPPER() in SQL to ensure case-insensitive comparison
                cursor.execute("""
                    SELECT name, job_token, invoice_name, job_data, media_types, printer_mac
                    FROM `tabCloudPRNT Print Queue`
                    WHERE UPPER(printer_mac) = %s AND status = 'Pending'
                    ORDER BY creation ASC
                    LIMIT 1
                """, (printer_mac_normalized,))

                job = cursor.fetchone()

                if not job:
                    print(f"[CloudPRNT] No jobs found for {printer_mac_normalized}")
                    return None

                print(f"[CloudPRNT] Found job: {job['job_token']} for printer {job['printer_mac']}")

                # Parse media types
                try:
                    media_types = json.loads(job.get("media_types") or "[]")
                except:
                    media_types = ["application/vnd.star.line", "text/vnd.star.markup"]

                return {
                    "name": job["name"],
                    "token": job["job_token"],
                    "invoice": job["invoice_name"],
                    "job_data": job["job_data"],
                    "media_types": media_types,
                    "printer_mac": printer_mac_normalized
                }
        finally:
            conn.close()

    except Exception as e:
        # Log the error instead of silently failing
        print(f"[CloudPRNT ERROR] Failed to get job for {printer_mac}: {str(e)}")
        import traceback
        traceback.print_exc()
        return None


def get_real_ip(request: Request) -> str:
    """Get real IP from X-Forwarded-For or direct connection"""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.post("/poll")
async def poll(request: Request):
    """
    CloudPRNT Poll Endpoint

    Printer polls to check for jobs and send status updates
    Accepts both / and /poll paths
    """
    try:
        init_frappe()

        # Get client IP
        client_ip = get_real_ip(request)

        # Parse JSON body
        try:
            data = await request.json()
        except:
            data = {}

        # Extract printer info
        printer_mac_dots = data.get("printerMAC", "")
        status_code = data.get("statusCode", "")
        printing_in_progress = data.get("printingInProgress", False)
        client_type = data.get("clientType", "")


        # Normalize MAC address
        printer_mac = normalize_mac_address(printer_mac_dots)

        print(f"[CloudPRNT] Poll from {client_ip}, MAC: {printer_mac_dots} -> normalized: {printer_mac}, status: {status_code}")

        if not printer_mac:
            print(f"[CloudPRNT WARNING] Invalid MAC address: {printer_mac_dots}")
            return JSONResponse({
                "jobReady": False,
                "mediaTypes": ["application/vnd.star.line", "text/vnd.star.markup"]
            })

        # Track for discovery
        try:
            import printer_discovery
            track_printer_poll = printer_discovery.track_printer_poll
            track_printer_poll(
                printer_mac,
                ip_address=client_ip,
                client_type=client_type,
                status_code=status_code
            )
        except Exception as e:
            # Don't fail if discovery tracking fails
            print(f"Discovery tracking error: {e}")

        # Check for jobs in database queue
        job = get_next_job_for_printer(printer_mac)

        print(f"[CloudPRNT] Job result: {job}")

        if job:
            print(f"[CloudPRNT] Returning jobReady=True for token: {job['token']}")
            return JSONResponse({
                "jobReady": True,
                "mediaTypes": job.get("media_types", ["application/vnd.star.line", "text/vnd.star.markup"]),
                "jobToken": job["token"]
            })
        else:
            print(f"[CloudPRNT] Returning jobReady=False (no job found)")
            return JSONResponse({
                "jobReady": False,
                "mediaTypes": ["application/vnd.star.line", "text/vnd.star.markup"]
            })

    except Exception as e:
        print(f"Error in poll endpoint: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({
            "jobReady": False,
            "mediaTypes": ["application/vnd.star.line", "text/vnd.star.markup"]
        })


@app.get("/job")
async def get_job(mac: str = Query(..., description="Printer MAC address in dot format")):
    """
    CloudPRNT Job Endpoint
    Accepts both / and /job paths

    Returns the job data for printing
    """
    try:
        init_frappe()

        # Normalize MAC
        printer_mac_dots = mac
        printer_mac = normalize_mac_address(printer_mac_dots)

        if not printer_mac:
            return Response(content="Invalid MAC address", status_code=400)

        # Get job from database queue
        job = get_next_job_for_printer(printer_mac)

        if not job:
            return Response(content="No job available", status_code=404)

        job_token = job["token"]

        # Mark job as fetched using direct MySQL
        try:
            import pymysql
            import json
            import os

            site_config_path = os.path.join(bench_path, "sites", "prod.local", "site_config.json")
            with open(site_config_path, 'r') as f:
                site_config = json.load(f)

            conn = pymysql.connect(
                host=site_config.get('db_host', 'localhost'),
                user=site_config.get('db_user', site_config.get('db_name', 'root')),
                password=site_config.get('db_password', ''),
                database=site_config.get('db_name'),
                charset='utf8mb4'
            )

            try:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        UPDATE `tabCloudPRNT Print Queue`
                        SET status = 'Fetched'
                        WHERE job_token = %s
                    """, (job_token,))
                    conn.commit()
            finally:
                conn.close()
        except Exception as e:
            print(f"Error marking job as fetched: {e}")

        # Check if job contains pre-converted hex data (from CPUtil image conversion or custom jobs)
        # These jobs have job_data as raw hex string that should be sent directly to printer
        media_types = job.get("media_types", [])

        # If job has job_data and it looks like hex data (no markup tags), return it directly
        if job.get("job_data"):
            job_data = job["job_data"]

            # Check if this is raw hex data (no Star Markup tags like [align:], [cut:], etc.)
            # Hex data will only contain [0-9A-Fa-f] characters
            is_hex_only = all(c in '0123456789ABCDEFabcdef\n\r ' for c in job_data[:100])

            if is_hex_only and len(job_data) > 100:  # Raw hex data (image or pre-converted)
                try:
                    binary_data = bytes.fromhex(job_data)

                    # Use first media type from the list
                    media_type = media_types[0] if media_types else "application/vnd.star.line"

                    return Response(
                        content=binary_data,
                        media_type=media_type,
                        headers={
                            "Content-Type": media_type,
                            "Content-Length": str(len(binary_data))
                        }
                    )
                except Exception as e:
                    print(f"Error converting hex to binary: {e}")
                    import traceback
                    traceback.print_exc()
                    return Response(content=f"Error processing hex job: {str(e)}", status_code=500)

        # Generate Star Line Mode binary for markup jobs (test and invoice)
        try:
            # Import print_job dynamically to avoid hooks errors
            import importlib.util
            spec = importlib.util.spec_from_file_location("print_job",
                os.path.join(cloudprnt_path, "print_job.py"))
            print_job_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(print_job_module)
            StarCloudPRNTStarLineModeJob = print_job_module.StarCloudPRNTStarLineModeJob

            # Get markup text
            if job.get("job_data"):
                # Test job - use job_data as markup
                markup_text = job["job_data"]
            elif job.get("invoice"):
                # Regular invoice job - get markup from invoice
                from pos_invoice_markup import get_pos_invoice_markup
                markup_text = get_pos_invoice_markup(job["invoice"])
            else:
                return Response(content="No job data or invoice", status_code=400)

            # Create Star Line Mode job
            printer_meta = {'printerMAC': mac_to_dots(printer_mac)}
            star_job = StarCloudPRNTStarLineModeJob(printer_meta)

            # Parse markup and build job
            lines = markup_text.split('\n')

            for line in lines:
                # Alignment tags
                if "[align: centre]" in line or "[align: center]" in line:
                    star_job.set_text_center_align()
                elif "[align: left]" in line:
                    star_job.set_text_left_align()
                elif "[align: right]" in line:
                    star_job.set_text_right_align()

                # Bold/emphasized tags
                if "[magnify:" in line or "[bold: on]" in line:
                    star_job.set_text_emphasized()
                elif "[magnify]" in line or "[bold: off]" in line:
                    star_job.cancel_text_emphasized()

                # Image tags - [image: url URL; ...]
                if "[image:" in line:
                    import re as img_re
                    img_match = img_re.search(r'\[image:\s*url\s+([^\s;]+)', line)
                    if img_match:
                        image_url = img_match.group(1)
                        try:
                            star_job.add_image_from_url(image_url)
                        except Exception as e:
                            print(f"Error adding image from URL {image_url}: {e}")
                    continue

                # Feed tags - handle both [feed] and [feed: length Xmm]
                if "[feed" in line:
                    # Extract feed length if specified (e.g., "[feed: length 3mm]")
                    import re as feed_re
                    feed_match = feed_re.search(r'\[feed:\s*length\s+(\d+)mm\]', line)
                    if feed_match:
                        # Convert mm to lines (roughly 1mm = 0.3 lines, so 3mm = 1 line)
                        mm = int(feed_match.group(1))
                        lines_to_feed = max(1, mm // 3)
                        star_job.add_new_line(lines_to_feed)
                    else:
                        # Simple [feed] tag
                        star_job.add_new_line(1)

                # Cut tags
                if "[cut" in line:
                    star_job.cut()
                    continue

                # Clean text - remove all tags and add line if not empty
                clean_text = re.sub(r'\[([^\]]+)\]', '', line)
                if clean_text.strip():
                    star_job.add_text_line(clean_text)

            # Get binary data from job builder
            hex_data = star_job.print_job_builder
            binary_data = bytes.fromhex(hex_data)

            return Response(
                content=binary_data,
                media_type="application/vnd.star.line",
                headers={
                    "Content-Type": "application/vnd.star.line",
                    "Content-Length": str(len(binary_data))
                }
            )
        except Exception as e:
            print(f"Error generating Star Line Mode job: {e}")
            import traceback
            traceback.print_exc()
            return Response(content=f"Error generating job: {str(e)}", status_code=500)

    except Exception as e:
        print(f"Error in job endpoint: {e}")
        import traceback
        traceback.print_exc()
        return Response(content=f"Error: {str(e)}", status_code=500)


@app.delete("/job")
async def delete_job(
    mac: str = Query(..., description="Printer MAC address in dot format"),
    token: str = Query(None, description="Job token to delete"),
    code: str = Query(None, description="Status code from printer")
):
    """
    CloudPRNT Delete Endpoint
    Accepts both / and /job paths

    Confirms job completion and removes it from queue
    """
    try:
        init_frappe()

        # Normalize MAC
        printer_mac_dots = mac
        printer_mac = normalize_mac_address(printer_mac_dots)

        if not printer_mac:
            return JSONResponse({"message": "Invalid MAC address"}, status_code=400)

        # Use token if provided, otherwise get next job
        job_token = token
        if not job_token:
            job = get_next_job_for_printer(printer_mac)
            if job:
                job_token = job["token"]

        if job_token:
            # Mark job as printed (delete from queue) using direct MySQL
            try:
                import pymysql
                import json
                import os

                site_config_path = os.path.join(bench_path, "sites", "prod.local", "site_config.json")
                with open(site_config_path, 'r') as f:
                    site_config = json.load(f)

                conn = pymysql.connect(
                    host=site_config.get('db_host', 'localhost'),
                    user=site_config.get('db_user', site_config.get('db_name', 'root')),
                    password=site_config.get('db_password', ''),
                    database=site_config.get('db_name'),
                    charset='utf8mb4'
                )

                try:
                    with conn.cursor() as cursor:
                        cursor.execute("""
                            DELETE FROM `tabCloudPRNT Print Queue`
                            WHERE job_token = %s
                        """, (job_token,))
                        conn.commit()
                        return JSONResponse({"message": "ok"})
                finally:
                    conn.close()

            except Exception as e:
                print(f"Error marking job as printed: {e}")
                import traceback
                traceback.print_exc()
                return JSONResponse({"message": f"Error: {str(e)}"}, status_code=500)
        else:
            return JSONResponse({"message": "No job to delete"}, status_code=404)

    except Exception as e:
        print(f"Error in delete endpoint: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"message": f"Error: {str(e)}"}, status_code=500)


@app.api_route("/", methods=["POST", "GET", "DELETE"])
async def root_endpoint(
    request: Request,
    mac: str = Query(None),
    token: str = Query(None),
    code: str = Query(None)
):
    """
    Root endpoint that routes to appropriate handler based on HTTP method
    Allows printer to use https://domain/cloudprnt/ for all operations
    """
    if request.method == "POST":
        return await poll(request)
    elif request.method == "GET":
        if not mac:
            return Response(content="MAC address required", status_code=400)
        return await get_job(mac)
    elif request.method == "DELETE":
        if not mac:
            return Response(content="MAC address required", status_code=400)
        return await delete_job(mac, token, code)
    else:
        return Response(content="Method not allowed", status_code=405)


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return JSONResponse({
        "status": "ok",
        "timestamp": datetime.now().isoformat(),
        "queued_jobs": sum(len(jobs) for jobs in PRINT_QUEUE.values())
    })


if __name__ == "__main__":
    print("=" * 80)
    print("CloudPRNT Standalone Server")
    print("=" * 80)
    print(f"Starting server on http://0.0.0.0:8001")
    print(f"Site: prod.local")
    print(f"Endpoints:")
    print(f"  - POST http://0.0.0.0:8001/poll")
    print(f"  - GET  http://0.0.0.0:8001/job?mac=XX.XX.XX.XX.XX.XX")
    print(f"  - DELETE http://0.0.0.0:8001/job?mac=XX.XX.XX.XX.XX.XX")
    print(f"  - GET  http://0.0.0.0:8001/health")
    print("=" * 80)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8001,
        log_level="info",
        access_log=True
    )
