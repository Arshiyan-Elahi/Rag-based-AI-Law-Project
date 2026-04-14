import paramiko
import sys

HOST = '65.21.244.158'
USER = 'root'
PASS = 'Cph181ko!!'

def run():
    print("Connecting via paramiko to run deploy_audit.sh...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(HOST, username=USER, password=PASS)
        
        sftp = ssh.open_sftp()
        sftp.put('deploy_audit.sh', '/tmp/deploy_audit.sh')
        sftp.close()

        ssh.exec_command('chmod +x /tmp/deploy_audit.sh')
        stdin, stdout, stderr = ssh.exec_command('bash /tmp/deploy_audit.sh')
        # Use errors='replace' to avoid charmap codec issues on Windows shell
        out = stdout.read().decode('utf-8', errors='replace')
        sys.stdout.buffer.write(out.encode('utf-8'))
        
    except Exception as e:
        print(f"Failed: {e}", file=sys.stderr)
    finally:
        ssh.close()

if __name__ == '__main__':
    run()
