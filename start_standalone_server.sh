#!/bin/bash
# CloudPRNT Standalone Server Startup Script
# This server handles CloudPRNT protocol requests on port 8001

cd "$(dirname "$0")/../.."
BENCH_DIR=$(pwd)

echo "Starting CloudPRNT Standalone Server..."
echo "Bench directory: $BENCH_DIR"

# Kill existing instance if running
pkill -f cloudprnt_standalone_server.py

# Wait a moment
sleep 2

# Start the server
nohup ./env/bin/python -u apps/cloudprnt/cloudprnt/cloudprnt_standalone_server.py > logs/cloudprnt_standalone.log 2>&1 &

echo "Server started. PID: $!"
echo "Logs: $BENCH_DIR/logs/cloudprnt_standalone.log"
echo ""
echo "To check status: ps aux | grep cloudprnt_standalone"
echo "To view logs: tail -f $BENCH_DIR/logs/cloudprnt_standalone.log"
