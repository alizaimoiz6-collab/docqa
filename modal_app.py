"""Deploy the API and the Streamlit UI to Modal.

    modal secret create anthropic-secret ANTHROPIC_API_KEY=sk-ant-...
    modal deploy modal_app.py

Modal prints two public URLs: `...-api.modal.run` (FastAPI, docs at /docs) and
`...-ui.modal.run` (Streamlit).
"""

import os
import subprocess

import modal

app = modal.App("docqa")

api_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_requirements("requirements.txt")
    .env({"DOCQA_STORE": "modal"})  # share uploads across containers via modal.Dict
    .add_local_python_source("docqa")
)

ui_image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install_from_requirements("requirements-frontend.txt")
    .add_local_file("frontend/streamlit_app.py", "/root/streamlit_app.py")
)


@app.function(
    image=api_image,
    secrets=[modal.Secret.from_name("anthropic-secret")],
    timeout=300,
    scaledown_window=300,
    max_containers=2,  # caps Modal compute spend under a traffic flood
)
@modal.concurrent(max_inputs=20)
@modal.asgi_app()
def api():
    from docqa.server import app as fastapi_app

    return fastapi_app


@app.function(image=ui_image, timeout=3600, scaledown_window=300, max_containers=1)
@modal.concurrent(max_inputs=50)
@modal.web_server(8000, startup_timeout=120)
def ui():
    env = {**os.environ, "API_URL": os.environ.get("API_URL") or api.get_web_url()}
    subprocess.Popen(
        [
            "streamlit", "run", "/root/streamlit_app.py",
            "--server.port", "8000",
            "--server.address", "0.0.0.0",
            "--server.headless", "true",
            "--server.enableCORS", "false",
            "--server.enableXsrfProtection", "false",
            "--browser.gatherUsageStats", "false",
            "--server.maxUploadSize", "20",  # matches the API's MAX_UPLOAD_MB
        ],
        env=env,
    )
