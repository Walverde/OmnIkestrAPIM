from fastapi import FastAPI

app = FastAPI(
    title="OmnIkestrAPIM API",
    version="0.1.0",
    description="Core API for OmnIkestrAPIM Orchestrator"
)

@app.get("/api/health")
def health_check():
    return {
        "status": "healthy",
        "service": "OmnIkestrAPIM Core Backend",
        "version": "0.1.0"
    }

@app.get("/")
def read_root():
    return {"message": "Welcome to OmnIkestrAPIM API"}