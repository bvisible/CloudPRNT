"""
Bench commands for CloudPRNT
"""
import click
import os
import sys
import subprocess


@click.command('run-cloudprnt-server')
@click.option('--site', default='prod.local', help='Site name')
def run_cloudprnt_server(site):
	"""
	Start CloudPRNT standalone server

	Usage:
		bench --site prod.local run-cloudprnt-server
	"""
	# Get bench path (ensure we're in bench root, not sites directory)
	bench_path = os.getcwd()
	if bench_path.endswith('/sites'):
		bench_path = os.path.dirname(bench_path)

	# Path to standalone server
	server_path = os.path.join(bench_path, 'apps', 'cloudprnt', 'cloudprnt', 'cloudprnt_standalone_server.py')

	if not os.path.exists(server_path):
		click.echo(f"Error: CloudPRNT server not found at {server_path}")
		sys.exit(1)

	# Python executable
	python_bin = os.path.join(bench_path, 'env', 'bin', 'python')

	click.echo("=" * 80)
	click.echo("Starting CloudPRNT Standalone Server")
	click.echo("=" * 80)
	click.echo(f"Site: {site}")
	click.echo(f"Port: 8001")
	click.echo(f"Press Ctrl+C to stop")
	click.echo("=" * 80)

	try:
		# Run the server
		subprocess.run([python_bin, server_path], cwd=bench_path)
	except KeyboardInterrupt:
		click.echo("\nServer stopped")
		sys.exit(0)


commands = [
	run_cloudprnt_server
]
