import os

import uvicorn

from src.web.server import app

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8181))
    print(f"Steam Friends Game Finder running at http://localhost:{port}")
    uvicorn.run(app, host="0.0.0.0", port=port, proxy_headers=True, forwarded_allow_ips="*")
