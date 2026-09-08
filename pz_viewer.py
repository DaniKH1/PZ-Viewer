import os
import sys
import threading
import time
import webbrowser

from viewer.server import run_server

def main():
    port = 8088
    use_browser = False

    for arg in sys.argv[1:]:
        if arg.isdigit():
            port = int(arg)
        elif arg in ('--browser', '-b'):
            use_browser = True

    url = f"http://127.0.0.1:{port}"
    print("=" * 65)
    print("  PROJECT ZERO / FATAL FRAME 1 - 3D VIEWER & EXTRACTOR (GPU)")
    print("=" * 65)
    print("  Mode: Native Desktop GUI Application")
    print(f"  Local Engine Address: {url}")
    print("  Rendering Pipeline: Hardware GPU Accelerated (VBO / Direct3D)")
    print("=" * 65)

    # Start local backend server in daemon thread
    server_thread = threading.Thread(target=run_server, args=(port,), daemon=True)
    server_thread.start()
    time.sleep(0.5)

    if use_browser:
        print(f"Opening in browser: {url} ...")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nExiting...")
            sys.exit(0)

    # Launch Desktop GUI Window via pywebview
    try:
        import webview
        print("Launching Desktop GUI Window (DirectX / GPU Accelerated) ...")
        window = webview.create_window(
            title="Project Zero / Fatal Frame 1 - 3D Viewer & Extractor (GPU)",
            url=url,
            width=1400,
            height=880,
            resizable=True,
            min_size=(1024, 680),
            background_color="#121316"
        )
        webview.start(gui='edgechromium', debug=False)
        print("Desktop GUI Window closed. Goodbye!")
    except Exception as e:
        print(f"Native Desktop GUI notice: {e}")
        print(f"Opening in system browser instead: {url}")
        webbrowser.open(url)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\nExiting...")

if __name__ == '__main__':
    main()
