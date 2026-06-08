import os
from dotenv import load_dotenv

load_dotenv()

FMP_API_KEY = os.getenv("FMP_API_KEY")

if not FMP_API_KEY:
    raise EnvironmentError("FMP_API_KEY not set. Add it to your .env file.")
