# Project AI

FastAPI-based Python project.

## Quickstart

```bash
# Create virtual env & install
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run
uvicorn app.main:app --reload

# Visit
open http://127.0.0.1:8000/docs
```

## Tests

```bash
pytest -v
```
