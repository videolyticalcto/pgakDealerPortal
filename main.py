import threading
from app import create_app
from app.config import Config
from app.socket_hub import start_socket_hub
from app.blueprints.devices.routes import inactive_watcher

app = create_app()

if __name__ == "__main__":
    print(f"[DEBUG] Registered routes: {len(list(app.url_map.iter_rules()))}")
    for rule in app.url_map.iter_rules():
        print(f"  {rule.rule} -> {rule.endpoint} [{','.join(rule.methods)}]")

    # Start background threads
    threading.Thread(target=inactive_watcher, daemon=True).start()
    start_socket_hub()

    import os
    cert_file = os.path.join(os.path.dirname(__file__), 'selfsigned.crt')
    key_file = os.path.join(os.path.dirname(__file__), 'selfsigned.key')
    ssl_ctx = None
    if os.path.exists(cert_file) and os.path.exists(key_file):
        ssl_ctx = (cert_file, key_file)
        print(f"[SSL] Running with HTTPS (cert={cert_file})")

    app.run(
        host="0.0.0.0",
        port=Config.SERVER_PORT,
        debug=True,
        use_reloader=False,
        ssl_context=ssl_ctx,
    )
