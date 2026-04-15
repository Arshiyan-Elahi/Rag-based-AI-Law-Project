# coding=utf-8
import paramiko

HOST = '65.21.244.158'
USER = 'root'
PASS = 'Cph181ko!!'

def verify_server_routes():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(HOST, username=USER, password=PASS)
        # Test script to list all registered routes in FastAPI app
        test_script = """
import sys
import os
sys.path.append('/opt/hybrid-rag-isolated/backend')
from app.main import app
for route in app.routes:
    # Print path and methods
    print(f"{route.path} -> {route.methods if hasattr(route, 'methods') else 'no-method'}")
"""
        # Save to temp on server
        stdin, stdout, stderr = ssh.exec_command(f'echo "{test_script}" > /tmp/list_routes.py')
        # Run it with the venv python
        stdin, stdout, stderr = ssh.exec_command('/opt/hybrid-rag-isolated/backend/venv/bin/python3 /tmp/list_routes.py')
        print(stdout.read().decode('utf-8'))
        err = stderr.read().decode('utf-8')
        if err: print(f"ERROR: {err}")
        
    except Exception as e:
        print(f"Verification failed: {e}")
    finally:
        ssh.close()

if __name__ == '__main__':
    verify_server_routes()
