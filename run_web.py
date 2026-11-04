import uvicorn

from src.web import settings
from src.web.server import app

if __name__ == "__main__":
    print(f"Steam Friends Game Finder running at http://localhost:{settings.PORT}")
    uvicorn.run(app, host=settings.HOST, port=settings.PORT, proxy_headers=True, forwarded_allow_ips="*")
