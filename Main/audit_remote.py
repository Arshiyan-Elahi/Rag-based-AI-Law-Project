# coding=utf-8
import paramiko
import sys

HOST = '65.21.244.158'
USER = 'root'
PASS = 'Cph181ko!!'

def audit_remote():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(HOST, username=USER, password=PASS)
        
        # Use simple commands that avoid special characters to prevent encoding issues on Windows terminal
        commands = [
            "ls -la /opt/hybrid-rag-isolated/backend",
            "ls -la /opt/hybrid-rag-isolated/frontend",
            "cat /etc/systemd/system/cybrain-backend.service",
            "cat /etc/nginx/sites-enabled/*"
        ]
        
        for cmd in commands:
            print(f"\n--- Output of: {cmd} ---")
            stdin, stdout, stderr = ssh.exec_command(cmd)
            # Use safe printing by ignoring or replacing non-standard characters
            out = stdout.read().decode('utf-8', errors='ignore')
            print(out)
            err = stderr.read().decode('utf-8', errors='ignore')
            if err: print(f"ERROR: {err}")
            
    except Exception as e:
        print(f"Audit failed: {e}")
    finally:
        ssh.close()

if __name__ == '__main__':
    audit_remote()
