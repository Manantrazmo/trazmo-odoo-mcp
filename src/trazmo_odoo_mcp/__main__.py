import os

import uvicorn

from .server import create_app_from_env

if __name__ == "__main__":
    uvicorn.run(
        create_app_from_env(),
        host=os.environ.get("HOST", "127.0.0.1"),  # local-only by default; the container sets HOST=0.0.0.0
        port=int(os.environ.get("PORT", "8080")),
        log_level="warning",
        access_log=False,  # never log request lines/headers
    )
