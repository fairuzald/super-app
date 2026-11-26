"""PyInstaller entry point for the Windows build."""
import sys

from superapp_hub.app import main

if __name__ == "__main__":
    sys.exit(main())
