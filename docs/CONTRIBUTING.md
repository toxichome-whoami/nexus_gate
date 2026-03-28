# Contributing to NexusGate

First off, thank you for considering contributing to NexusGate! It's people like you that make NexusGate such a great tool.

## 1. Code of Conduct

Help us keep NexusGate open and inclusive. Please read and follow our [Code of Conduct](CODE_OF_CONDUCT.md).

## 2. Getting Started

- **Fork the Repository**: Create your own copy of the project.
- **Clone Local**: `git clone https://github.com/your-username/nexus_gate.git`
- **Environment**: Use a virtual environment.
  ```bash
  python -m venv .venv
  source .venv/bin/activate  # or .venv\Scripts\activate on Windows
  pip install -r requirements.txt
  ```

## 3. Development Workflow

1.  **Create a Branch**: `git checkout -b feature/your-feature-name`
2.  **Implementation**: Follow the strict `src/` layout.
3.  **Testing**: NexusGate uses `pytest`. Ensure your changes pass all tests.
    ```bash
    pytest tests/
    ```
4.  **Linting**: We follow PEP8.
5.  **Documentation**: Update the relevant files in `docs/` if you change any public API or configuration.

## 4. Pull Request Process

1.  Synchronize your branch with the `main` branch.
2.  Write clear, descriptive commit messages.
3.  Fill out the Pull Request template completely.
4.  A maintainer will review your code and may suggest changes before merging.

## 5. Reporting Bugs

- Use the GitHub Issues tab.
- Describe the expected behavior and the actual behavior.
- Provide a minimal reproduction script if possible.
- Include your `config.toml` (with secrets REDACTED).

## 6. Financial Support

If you or your company find NexusGate useful, please consider sponsoring the project to ensure its long-term sustainability.
