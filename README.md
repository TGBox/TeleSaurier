# TeleSaurier

Eine Python Anwendung um Faxe bequem am PC zu versenden und zu empfangen.

## Tech-Stack

- **Sprache:** Python
- **Build / Packaging:** uv
- **CI/CD:** GitHub Actions
## Ordnerstruktur

```text
TeleSaurier/
├── src/
│   ├── __init__.py
│   ├── main.py
│   ├── build.py
│   └── tools.py
├── tests/
│   └── test_main.py
├── .github/workflows/ci.yml├── .gitignore
├── pyproject.toml
└── README.md
```

## Quickstart

```bash
git clone <repo-url>
cd TeleSaurier
uv sync

# App ausführen
uv run main
# oder:
uv run TeleSaurier

# Tests ausführen
uv run test

# Exe-Datei mit PyInstaller erstellen
uv run build
```