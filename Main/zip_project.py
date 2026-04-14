import os
import zipfile

def create_zip():
    print("Creating zip file for deployment...")
    zip_path = 'deploy_package.zip'
    if os.path.exists(zip_path):
        os.remove(zip_path)
        
    include_dirs = ['src', 'backend', 'public', 'dist']
    include_files = ['package.json', 'package-lock.json', 'vite.config.js', 'index.html']
    
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for f in include_files:
            if os.path.exists(f):
                zipf.write(f)
                
        for d in include_dirs:
            if os.path.exists(d):
                for root, dirs, files in os.walk(d):
                    # exclude __pycache__ and node_modules if somehow nested
                    dirs[:] = [d for d in dirs if d not in ['__pycache__', 'node_modules', '.venv', 'venv']]
                    for file in files:
                        if file.endswith('.pyc'): continue
                        filepath = os.path.join(root, file)
                        zipf.write(filepath, filepath)
                        
    print(f"Created {zip_path} with size {os.path.getsize(zip_path)} bytes.")

if __name__ == '__main__':
    create_zip()
