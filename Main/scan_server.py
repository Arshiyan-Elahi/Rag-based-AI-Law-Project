# coding=utf-8
import paramiko

HOST = '65.21.244.158'
USER = 'root'
PASS = 'Cph181ko!!'

def scan_server():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(HOST, username=USER, password=PASS)
        # Find all routes.py files that DO NOT contain our new endpoint
        cmd = "find /opt/hybrid-rag-isolated -name routes.py"
        stdin, stdout, stderr = ssh.exec_command(cmd)
        files = stdout.read().decode('utf-8').splitlines()
        
        for f in files:
            # check stats endpoint
            cmd = f"grep -c 'def get_knowledge_stats' {f}"
            stdin, stdout, stderr = ssh.exec_command(cmd)
            count = stdout.read().decode('utf-8').strip()
            print(f"File: {f} -> count: {count}")
            
    except Exception as e:
        print(f"Failed: {e}")
    finally:
        ssh.close()

if __name__ == '__main__':
    scan_server()
