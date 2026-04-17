import os
import zipfile

def create_zip():
    print("Creating zip file for deployment...")
    zip_path = 'deploy_package.zip'
    if os.path.exists(zip_path):
        os.remove(zip_path)

    # Only include what the server needs
    include_dirs = ['src', 'backend', 'public', 'dist']
    include_files = ['package.json', 'package-lock.json', 'vite.config.js', 'index.html']

    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for f in include_files:
            if os.path.exists(f):
                zipf.write(f)

        for d in include_dirs:
            if os.path.exists(d):
                for root, dirs, files in os.walk(d):
                    # Exclude unneeded directories
                    dirs[:] = [dd for dd in dirs if dd not in [
                        '__pycache__', 'node_modules', '.venv', 'venv', 
                        'review-needed', '.git'
                    ]]
                    for file in files:
                        if file.endswith('.pyc'):
                            continue
                        filepath = os.path.join(root, file)
                        zipf.write(filepath, filepath)

    size_kb = os.path.getsize(zip_path) // 1024
    print(f"Created {zip_path} ({size_kb} KB)")

if __name__ == '__main__':
    create_zip()
