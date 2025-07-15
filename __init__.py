"""
Netmig Script Template - Package Initializer

This `__init__.py` file serves as the standard entry point for Netmig-compatible scripts.
It ensures that the necessary components are exposed for the Netmig application to
recognize and load the script into its environment.

Purpose:
    - Exposes the `Form` class, which must be a subclass of `QtWidgets.QWidget`.
    - Acts as a contract: every script integrated into Netmig **must** provide a `Form`.

Requirements:
    - The `Form` class must inherit from `QtWidgets.QWidget`.
    - The Netmig tool uses this interface to embed the script's UI panel into its main application window.

Example Usage in Netmig:
    from myscript import Form

Developed for:
    Netmig Script Integration Framework
"""

# Mandatory module used by Netmig to load the script into the Netmig UI
from .ui import Form

