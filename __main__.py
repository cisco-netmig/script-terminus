"""
Netmig Script Template - Main Entry Point

This template serves as the main entry point for scripts that are to be integrated into
the Netmig platform. Authors of new scripts can use this template as a standard structure
to ensure compatibility with the Netmig environment.

Usage:
    python -m <script_name> [options]

Command-Line Arguments:
    --lib          : Paths to add to the Python module search path (JSON formatted list).
    --output       : Output directory (string).
    --session      : Session data as a JSON string (overrides configuration file).
    --config       : Path to the session configuration JSON file.
    --qss          : QSS stylesheet as a string.
    --style        : Styling options as a JSON string (including font and style settings).

Platform:
    The script detects the platform (Windows, macOS, Linux) and applies necessary
    platform-specific settings such as setting the process ID for Windows taskbar
    integration.

Dependencies:
    - PyQt5: For the GUI framework.
    - JSON: For configuration and session management.
    - argparse: For parsing command-line arguments.
    - logging: For application logging.
    - platform: For platform-specific logic.

Example:
    python -m myscript --session '{"username": "admin", "password"}'

"""

import os
import json
import sys
import argparse
import platform
import logging
from PyQt5 import QtWidgets, QtGui, QtCore
from .ui import Form

# Set up logging
logging.basicConfig(level=logging.INFO, stream=sys.stdout)

def main():
    """
    Main entry point for scripts to be integrated into the Netmig platform. This function parses
    command-line arguments, configures environment variables, sets up the main window, and starts
    the Qt application loop.
    """

    # Parsing command-line arguments
    parser = argparse.ArgumentParser(description="Netmig Script Command-Line Arguments")
    parser.add_argument('--lib', type=str, help="Paths to add to the Python module search path")
    parser.add_argument('--output', type=str, help="Output directory")
    parser.add_argument('--session', type=str, help="Session data as a JSON string")
    parser.add_argument('--config', type=str, help="Path to the session configuration JSON file")
    parser.add_argument('--qss', type=str, help="QSS stylesheet as a string")
    parser.add_argument('--style', type=str, help="Styling options as a JSON string")

    # Parse known arguments
    args, _ = parser.parse_known_args()

    # Initialize the Qt application
    app = QtWidgets.QApplication([])

    # Dictionary to store key arguments
    kwargs = {}

    # Update Python path with libraries from the arguments
    if args.lib:
        sys.path.extend(json.loads(args.lib))

    # Set the output directory for Netmig
    kwargs["output_dir"] = os.path.dirname(__file__)
    if args.output:
        kwargs["output_dir"] = args.output

    # Apply styling options if provided
    if args.style:
        styling = json.loads(args.style)
        app.setStyle(styling['style'])
        app.setFont(QtGui.QFont(*styling['font'].values()))

    # Apply QSS stylesheet if provided
    if args.qss:
        app.setStyleSheet(args.qss)

    # Load session data
    kwargs["session"] = {}
    if args.session:
        kwargs["session"] = json.loads(args.session)
    elif args.config:
        kwargs["session"] = json.load(open(args.config))['session']

    # Ensure that session data is available
    if not kwargs["session"]:
        logging.error("Either session arg or session configuration is not set!")
        sys.exit(1)

    # Set up the main window
    window = QtWidgets.QMainWindow()
    window.setWindowTitle(os.path.basename(os.path.dirname(__file__)).title())
    window.setWindowIcon(QtGui.QIcon(os.path.join(os.path.dirname(__file__), '__icon__.ico')))

    # Initialize the main form and set it as the central widget
    form = Form(parent=window, **kwargs)
    window.setCentralWidget(form)
    window.resize(800, 600)
    window.show()

    # Start the Qt event loop
    sys.exit(app.exec_())

if __name__ == '__main__':
    """
    Entry point for running the script. This will check the platform and set the
    process ID for Windows if applicable, and then execute the `main` function.
    """
    if platform.system() == 'Windows':
        # Set the AppUserModelID for Windows taskbar integration
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f'cisco.netmig.{__file__}')

    # Run the main application
    main()
