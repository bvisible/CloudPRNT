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
os.chdir(bench_path)

# Frappe will be initialized per-request to avoid connection issues
import frappe

app = FastAPI(title="CloudPRNT Standalone Server", version="1.0.0")

# Global variables for queue (will be populated from Redis)
PRINT_QUEUE = {}


def init_frappe():
    """Initialize Frappe connection for this request"""
    if not frappe.local.site:
        sites_path = os.path.join(bench_path, "sites")
        frappe.init(site="prod.local", sites_path=sites_path)
        frappe.connect()
        frappe.set_user("Administrator")


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


def get_print_queue():
    """Get print queue from Redis"""
    try:
        # Import here to avoid initialization issues
        from cloudprnt.cloudprnt_server import PRINT_QUEUE as QUEUE
        return QUEUE
    except:
        return PRINT_QUEUE


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

        if not printer_mac:
            return JSONResponse({
                "jobReady": False,
                "mediaTypes": ["application/vnd.star.line", "text/vnd.star.markup"]
            })

        # Track for discovery
        try:
            from cloudprnt.printer_discovery import track_printer_poll
            track_printer_poll(
                printer_mac,
                ip_address=client_ip,
                client_type=client_type,
                status_code=status_code
            )
        except Exception as e:
            # Don't fail if discovery tracking fails
            print(f"Discovery tracking error: {e}")

        # Check for jobs in queue
        queue = get_print_queue()
        if printer_mac in queue and len(queue[printer_mac]) > 0:
            job = queue[printer_mac][0]

            return JSONResponse({
                "jobReady": True,
                "mediaTypes": job.get("media_types", ["application/vnd.star.line", "text/vnd.star.markup"]),
                "jobToken": job["token"]
            })
        else:
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

    Returns the job data for printing
    """
    try:
        init_frappe()

        # Normalize MAC
        printer_mac_dots = mac
        printer_mac = normalize_mac_address(printer_mac_dots)

        if not printer_mac:
            return Response(content="Invalid MAC address", status_code=400)

        # Get job from queue
        queue = get_print_queue()
        if printer_mac not in queue or len(queue[printer_mac]) == 0:
            return Response(content="No job available", status_code=404)

        job = queue[printer_mac][0]
        job_token = job["token"]

        # Generate print data based on media type
        media_type = job.get("media_type", "application/vnd.star.line")

        if media_type == "application/vnd.star.line":
            # Generate Star Line Mode hex
            try:
                from cloudprnt.cloudprnt_server import generate_star_line_job
                hex_data = generate_star_line_job(job)
                # Convert hex string to bytes
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

        elif media_type == "text/vnd.star.markup":
            # Return markup directly
            markup = job.get("markup", "")
            return PlainTextResponse(
                content=markup,
                media_type="text/vnd.star.markup"
            )

        else:
            return Response(content="Unsupported media type", status_code=400)

    except Exception as e:
        print(f"Error in job endpoint: {e}")
        import traceback
        traceback.print_exc()
        return Response(content=f"Error: {str(e)}", status_code=500)


@app.delete("/job")
async def delete_job(mac: str = Query(..., description="Printer MAC address in dot format")):
    """
    CloudPRNT Delete Endpoint

    Confirms job completion and removes it from queue
    """
    try:
        init_frappe()

        # Normalize MAC
        printer_mac_dots = mac
        printer_mac = normalize_mac_address(printer_mac_dots)

        if not printer_mac:
            return JSONResponse({"message": "Invalid MAC address"}, status_code=400)

        # Remove job from queue
        queue = get_print_queue()
        if printer_mac in queue and len(queue[printer_mac]) > 0:
            removed_job = queue[printer_mac].pop(0)

            # Clean up empty queue
            if len(queue[printer_mac]) == 0:
                del queue[printer_mac]

            # Log the print
            try:
                frappe.set_user("Administrator")
                from cloudprnt.cloudprnt_server import create_print_log
                create_print_log(removed_job)
            except Exception as e:
                print(f"Error creating print log: {e}")

            return JSONResponse({"message": "ok"})
        else:
            return JSONResponse({"message": "No job to delete"}, status_code=404)

    except Exception as e:
        print(f"Error in delete endpoint: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"message": f"Error: {str(e)}"}, status_code=500)


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
