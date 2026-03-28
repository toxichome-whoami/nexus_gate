import os

import_replacements = [
    ("from nexusgate.", "from "),
    ("import nexusgate.", "import "),
    ("from nexusgate import", "from src import"), # edge cases
]

def process_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    original_content = content
    for old, new in import_replacements:
        content = content.replace(old, new)

    if content != original_content:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Updated: {filepath}")

def main():
    for root, dirs, files in os.walk("src"):
        for file in files:
            if file.endswith(".py"):
                process_file(os.path.join(root, file))
    
    # Process tests
    if os.path.exists("tests"):
        for root, dirs, files in os.walk("tests"):
            for file in files:
                if file.endswith(".py"):
                    process_file(os.path.join(root, file))

    # Process main files like Dockerfile
    for target in ["Dockerfile", "docker-compose.yml", "pyproject.toml"]:
        if os.path.exists(target):
            with open(target, 'r', encoding='utf-8') as f:
                content = f.read()
            original = content
            content = content.replace("nexusgate.main:app", "main:app")
            content = content.replace("python -m nexusgate", "python src/main.py")
            if content != original:
                with open(target, 'w', encoding='utf-8') as f:
                    f.write(content)

if __name__ == "__main__":
    main()
