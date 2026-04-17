# coding=utf-8
import paramiko
import sys
import os

os.environ['PYTHONIOENCODING'] = 'utf-8'

HOST = '65.21.244.158'
USER = 'root'
PASS = 'Cph181ko!!'

def run_cmd(ssh, cmd):
    stdin, stdout, stderr = ssh.exec_command(cmd)
    exit_status = stdout.channel.recv_exit_status()
    out = stdout.read().decode('utf-8', errors='replace')
    err = stderr.read().decode('utf-8', errors='replace')
    return exit_status, out, err

def deploy():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(HOST, username=USER, password=PASS, timeout=15)
        
        sftp = ssh.open_sftp()
        sftp.put('deploy_package.zip', '/tmp/deploy_package.zip')
        sftp.put('deploy_script.sh', '/tmp/deploy_script.sh')
        sftp.close()

        run_cmd(ssh, 'chmod +x /tmp/deploy_script.sh')
        status, out, err = run_cmd(ssh, 'bash /tmp/deploy_script.sh')
        
        # Write output to file to avoid console encoding issues
        with open('deploy_output.txt', 'w', encoding='utf-8') as f:
            f.write(out)
            if err:
                f.write("\n--- STDERR ---\n")
                f.write(err)
        
        print(f"Deploy exit code: {status}")
        print("Full output saved to deploy_output.txt")
        
        # Verification
        _, stats_out, _ = run_cmd(ssh, 'curl -s http://127.0.0.1:8000/api/stats')
        _, search_out, _ = run_cmd(ssh, 'curl -s http://127.0.0.1:8000/api/search?q=SOP')
        _, dec_out, _ = run_cmd(ssh, 'curl -s http://127.0.0.1:8000/api/search?q=decision')
        
        with open('deploy_output.txt', 'a', encoding='utf-8') as f:
            f.write(f"\n--- VERIFICATION ---\n")
            f.write(f"/api/stats: {stats_out.strip()}\n")
            f.write(f"/api/search?q=SOP results: {search_out.strip()[:300]}\n")
            f.write(f"/api/search?q=decision results: {dec_out.strip()[:300]}\n")
        
        print(f"Stats: {stats_out.strip()[:200]}")
        print(f"Search SOP: {search_out.count('type')} results")
        print(f"Search decision: {dec_out.count('type')} results")
        
    except Exception as e:
        print(f"Failed: {e}")
    finally:
        ssh.close()

if __name__ == '__main__':
    deploy()
