"""Tiny ledger dependency used to test dependency recovery through Docker."""
from fastapi import FastAPI

app = FastAPI(title="sentinel-ledger-dependency")


@app.get("/health")
def health():
    return {"service": "ledger-dependency", "status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8090, log_level="warning")
