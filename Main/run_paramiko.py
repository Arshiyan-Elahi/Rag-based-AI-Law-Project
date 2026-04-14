import paramiko
import time
import sys

HOST = '65.21.244.158'
USER = 'root'
PASS = 'Cph181ko!!'

def run_cmd(ssh, cmd):
    print(f"Running: {cmd}")
    stdin, stdout, stderr = ssh.exec_command(cmd)
    
    # Wait for the command to finish and print output
    exit_status = stdout.channel.recv_exit_status()        
    out = stdout.read().decode('utf-8')
    err = stderr.read().decode('utf-8')
    
    if out: print(out)
    if err: print(f"ERROR: {err}", file=sys.stderr)
    return exit_status, out

def deploy():
    print("Initiating deployment via python paramiko...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(HOST, username=USER, password=PASS)
        
        # Open SFTP
        print("Opening SFTP channel...")
        sftp = ssh.open_sftp()
        print("Uploading deploy_package.zip...")
        sftp.put('deploy_package.zip', '/tmp/deploy_package.zip')
        print("Uploading deploy_script.sh...")
        sftp.put('deploy_script.sh', '/tmp/deploy_script.sh')
        sftp.close()

        print("Executing deployment script on the server...")
        ssh.exec_command('chmod +x /tmp/deploy_script.sh')
        status, out = run_cmd(ssh, 'bash /tmp/deploy_script.sh')
        
    except Exception as e:
        print(f"Deployment failed: {e}", file=sys.stderr)
    finally:
        ssh.close()

if __name__ == '__main__':
    deploy()
