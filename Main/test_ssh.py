# coding=utf-8
import paramiko

HOST = '65.21.244.158'
USER = 'root'
PASS = 'Cph181ko!!'

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    ssh.connect(HOST, username=USER, password=PASS, timeout=10)
    print("SUCCESS: Connected to server!")
    stdin, stdout, stderr = ssh.exec_command('whoami')
    print(f"User: {stdout.read().decode('utf-8').strip()}")
    ssh.close()
except paramiko.AuthenticationException:
    print("FAILED: Authentication failed - password may have changed")
except Exception as e:
    print(f"FAILED: {e}")
