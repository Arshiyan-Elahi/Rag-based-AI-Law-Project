# coding=utf-8
import paramiko
import os

HOST = '65.21.244.158'
USER = 'root'
PASS = 'Cph181ko!!'

def verify_remote_file():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(HOST, username=USER, password=PASS)
        sftp = ssh.open_sftp()
        # Download the remote routes.py to a local scratch site
        sftp.get('/opt/hybrid-rag-isolated/backend/app/routes.py', 'remote_routes_audit.py')
        sftp.close()
        print("Successfully downloaded remote_routes_audit.py for inspection.")
    except Exception as e:
        print(f"Failed to fetch remote file: {e}")
    finally:
        ssh.close()

if __name__ == '__main__':
    verify_remote_file()
