from fastapi import FastAPI

app = FastAPI(title="SWI-LAB", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# CP1 walking skeleton (POST /reservations) sa implementuje az v C03.
# Presna definicia je v README.md, sekcia "CP1 walking skeleton".
