# Trading Room Backend

This is a FastAPI backend service providing asset information for Taiwan stocks, US stocks, and Futures.

## Setup

1. Create a virtual environment (optional but recommended):
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows use `venv\Scripts\activate`
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Running the Server

Start the development server:
```bash
uvicorn main:app --reload
```

The API will be available at `http://127.0.0.1:8000`.

## API Endpoints

- **Taiwan Stocks:** `GET /assets/tw-stocks`
- **US Stocks:** `GET /assets/us-stocks`
- **Futures:** `GET /assets/futures`

Interactive documentation is available at `http://127.0.0.1:8000/docs`.
