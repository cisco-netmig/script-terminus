import logging
logger = logging.getLogger(__name__)

import re
import os
import time
import threading
import platform
import logging
from termqt import Terminal
from PyQt5 import QtWidgets, QtCore, QtGui

from .workers import CommandThread, SSHThread

from termqt.terminal_io_posix import TerminalPOSIXExecIO as BasePOSIXIO
from termqt.terminal_io_windows import TerminalWinptyIO as BaseWinptyIO


class SafeTerminalPOSIXExecIO(BasePOSIXIO):
    def _read_loop(self):
        self.logger.info("[POSIX IO] Read loop started.")
        try:
            while self.running:
                try:
                    buf = self.pty_process.read()
                except EOFError:
                    self.logger.info("[POSIX IO] EOFError: PTY is closed.")
                    break
                except Exception as e:
                    self.logger.error(f"[POSIX IO] Unexpected error: {e}")
                    break

                if not buf:
                    continue

                if isinstance(buf, str):
                    buf = buf.encode("utf-8")

                try:
                    self.stdout_callback(buf)
                except Exception as cb_err:
                    self.logger.warning(f"[POSIX IO] stdout_callback error: {cb_err}")
        finally:
            self.logger.info("[POSIX IO] Read loop exiting.")
            if self.running:
                self.running = False
            try:
                self.terminated_callback()
            except Exception as term_err:
                self.logger.warning(f"[POSIX IO] terminated_callback error: {term_err}")


class SafeTerminalWinptyIO(BaseWinptyIO):
    def _read_loop(self):
        self.logger.info("[WinPTY] Read loop started.")
        try:
            while self.running:
                try:
                    buf = self.pty_process.read()
                except EOFError:
                    self.logger.info("[WinPTY] EOFError: PTY is closed.")
                    break
                except Exception as e:
                    self.logger.error(f"[WinPTY] Unexpected error while reading: {e}")
                    break

                if not buf:
                    continue

                if isinstance(buf, str):
                    buf = buf.encode("utf-8")

                try:
                    self.stdout_callback(buf)
                except Exception as cb_err:
                    self.logger.warning(f"[WinPTY] stdout_callback failed: {cb_err}")
        finally:
            self.logger.info("[WinPTY] Read loop exiting. Cleaning up...")
            if self.running:
                self.running = False
            try:
                self.terminated_callback()
            except Exception as term_err:
                self.logger.warning(f"[WinPTY] terminated_callback error: {term_err}")


class SpawnTerminal(Terminal):
    """
    A cross-platform terminal wrapper supporting interactive command execution and SSH connections.
    Uses winpty on Windows and exec-based IO on POSIX.
    """
    BASE_PROMPT_REGEX = rb"[>#\$]\s*$"
    ANSI_ESCAPE_REGEX = re.compile(rb'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    terminated = QtCore.pyqtSignal()

    def __init__(self, width=800, height=600, form=None, *args, **kwargs):
        super().__init__(width=width, height=height, *args, **kwargs)
        self.form = form
        self.maximum_line_history = 2000
        self.io = None
        self.auto_wrap_enabled = True
        self.output_buffer = b""
        self.buffer_lock = threading.Lock()
        self._last_cmd_thread = None
        self._last_ssh_thread = None
        self._apply_style()
        self._setup_io()
        self._connect_io()

    def _apply_style(self):
        """Set terminal style using global palette *before spawn*"""
        palette = QtWidgets.QApplication.palette()
        bg_color = palette.color(palette.Window)
        fg_color = palette.color(palette.Text)
        self.set_bg(bg_color)
        self.set_fg(fg_color)

    def _setup_io(self):
        """Initialize platform-specific IO backend with safe subclassed read loops."""
        system = platform.system()
        logger.info(f"Setting up terminal IO for platform: {system}")

        if system in ["Linux", "Darwin"]:
            shell_bin = "/bin/bash"
            self.io = SafeTerminalPOSIXExecIO(self.row_len, self.col_len, shell_bin)
        elif system == "Windows":
            shell_bin = "cmd"
            self.io = SafeTerminalWinptyIO(self.row_len, self.col_len, shell_bin)
            self.auto_wrap_enabled = False
        else:
            raise OSError(f"Unsupported platform: {system}")

    def _connect_io(self):
        """Connect IO callbacks and spawn shell."""
        self.enable_auto_wrap(self.auto_wrap_enabled)

        def on_output(data: bytes):
            self.stdout(data)
            with self.buffer_lock:
                self.output_buffer += data

        self.io.stdout_callback = on_output
        self.stdin_callback = self.io.write
        self.resize_callback = self.io.resize
        self.io.spawn()
        self.io.terminated_callback = self._on_terminal_terminated
        logger.info("Terminal IO spawned and connected.")

    def _on_terminal_terminated(self):
        logger.info("SpawnTerminal: Terminal process ended.")
        self.terminated.emit()

    def _read_until(self, pattern: bytes, timeout: int = 10) -> bytes:
        """
        Read output until a regex pattern is matched or timeout occurs.

        Args:
            pattern: Byte regex to match against cleaned output.
            timeout: Timeout in seconds.

        Returns:
            Raw output collected until the pattern matched or timeout.
        """
        end_time = time.time() + timeout
        collected = b""
        pattern = pattern.encode() if isinstance(pattern, str) else pattern

        logger.debug(f"Waiting for pattern: {pattern} (timeout={timeout})")

        while time.time() < end_time:
            with self.buffer_lock:
                if self.output_buffer:
                    collected += self.output_buffer
                    self.output_buffer = b""

            cleaned = self.ANSI_ESCAPE_REGEX.sub(b"", collected)
            if re.search(pattern, cleaned):
                break
            time.sleep(0.1)

        return collected

    def _send_command_blocking(self, command: str, expect: bytes, timeout: int) -> bytes:
        """
        Send a command and wait for expected output synchronously.

        Args:
            command: Command to send.
            expect: Regex pattern to wait for.
            timeout: Timeout in seconds.

        Returns:
            Bytes output until expected pattern matched or timeout.
        """
        self.io.write((command + '\r\n').encode('utf-8'))
        return self._read_until(expect, timeout)

    def _update_tab_title(self, prompt):
        parent = self.parent()
        while parent and not isinstance(parent, QtWidgets.QTabWidget):
            parent = parent.parent()
        if isinstance(parent, QtWidgets.QTabWidget):
            index = parent.indexOf(self)
            if index != -1:
                parent.setTabText(index, prompt)

    def send_command(self, command, expect=None, timeout=10, callback=None, error_callback=None):
        """
        Send a command asynchronously using a background thread.

        Args:
            command: Command string to send.
            expect: Byte regex pattern to wait for.
            timeout: Timeout in seconds.
            callback: Function to call on success with result bytes.
            error_callback: Function to call on failure with error message.
        """
        logger.info(f"Starting async command: {command}")
        thread = CommandThread(self, command, expect or self.BASE_PROMPT_REGEX, timeout)

        def cleanup():
            thread.deleteLater()

        def on_success(prompt_bytes):
            prompt = prompt_bytes.decode("utf-8")
            if callback:
                callback(prompt)

        def on_error(err):
            if error_callback:
                error_callback(err)

        thread.finished.connect(on_success)
        thread.error.connect(on_error)
        thread.finished.connect(cleanup)
        thread.error.connect(cleanup)

        thread.start()
        self._last_cmd_thread = thread

    def open_ssh(self,
                 hostname, username, password,
                 jumphost_hostname=None, jumphost_username=None, jumphost_password=None,
                 timeout=15,
                 callback=None,
                 error_callback=None):
        """
        Open SSH session, optionally via a jumphost.

        Args:
            hostname: Remote target hostname.
            username: Remote user.
            password: Remote password.
            jumphost_hostname: Jumphost hostname (optional).
            jumphost_username: Jumphost user (optional).
            jumphost_password: Jumphost password (optional).
            timeout: Timeout in seconds.
            callback: Function to call with final output.
            error_callback: Function to call on error.
        """

        thread = SSHThread(self, hostname, username, password, jumphost_hostname, jumphost_username, jumphost_password, timeout)
        
        def cleanup():
            thread.deleteLater()

        def on_success(prompt_bytes):
            prompt = prompt_bytes.decode("utf-8")
            self._update_tab_title(prompt)
            self.start_logging(label=prompt, file_path=self.form.output_dir)

            if callback:
                callback(prompt)

        def on_error(err):
            if error_callback:
                error_callback(err)

        thread.finished.connect(on_success)
        thread.error.connect(on_error)
        thread.finished.connect(cleanup)
        thread.error.connect(cleanup)

        thread.start()
        self._last_ssh_thread = thread

    def start_logging(self, label="terminal", file_path=None):
        """
        Start logging terminal output to a file.

        Args:
            label (str): Label to include in the filename (e.g., "cmd", "R1").
            file_path (str): Optional full path or directory where log file will be saved.
                             If a directory is given, filename will be auto-generated.
        """

        CLEAN_OUTPUT_RE = re.compile(
            rb'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])|[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F]'
        )

        timestamp = time.strftime("%Y-%m-%d_%H.%M.%S")
        safe_name = re.sub(r"[^\w\-]", "_", label)
        filename = f"{safe_name}_{timestamp}.log"

        if file_path is None:
            file_path = os.path.join(os.getcwd(), filename)
        elif os.path.isdir(file_path):
            file_path = os.path.join(file_path, filename)

        self.log_file_path = file_path

        self._original_stdout_callback = self.io.stdout_callback

        def tee_stdout(data: bytes):
            if self._original_stdout_callback:
                self._original_stdout_callback(data)

            try:
                cleaned = CLEAN_OUTPUT_RE.sub(b'', data)
                with open(self.log_file_path, "ab") as f:
                    f.write(cleaned)
            except Exception as e:
                logger.warning(f"[Terminus] Failed to write log: {e}")

        self.io.stdout_callback = tee_stdout
        logger.info(f"[Terminus] Logging started: {self.log_file_path}")


class Form(QtWidgets.QWidget):
    """
    UI Form class.
    """
    def __init__(self, parent=None, **kwargs):
        """
        Initialize the UI form.

        Args:
            parent (QWidget): Parent widget.
            **kwargs: Additional arguments for customization or metadata.
        """
        super().__init__(parent)
        self.kwargs = kwargs
        self.session = kwargs.get("session")
        self.output_dir = os.path.join(self.kwargs.get("output_dir"),
                                       os.path.basename(os.path.dirname(__file__).upper()))

        self._init_layout()
        self._init_controls()
        self._init_tabs()
        self._init_command_bar()

    def _init_layout(self):
        """Initialize main layout."""
        self.layout = QtWidgets.QVBoxLayout(self)
        self.layout.setContentsMargins(5, 5, 5, 5)
        self.layout.setSpacing(5)

    def _init_controls(self):
        """Initialize control buttons (New Terminal, SSH Launcher)."""
        self.action_layout = QtWidgets.QHBoxLayout()
        self.action_layout.setContentsMargins(0, 0, 0, 0)
        self.action_layout.setSpacing(5)

        self.new_terminal_button = QtWidgets.QPushButton("New Terminal")
        self.new_terminal_button.setFixedSize(150, 30)
        self.new_terminal_button.setIcon(self._get_icon("add-tab"))
        self.new_terminal_button.clicked.connect(lambda: self.add_new_terminal())
        self.action_layout.addWidget(self.new_terminal_button)

        self.ssh_launcher_button = QtWidgets.QPushButton("SSH Launcher")
        self.ssh_launcher_button.setFixedSize(150, 30)
        self.ssh_launcher_button.setIcon(self._get_icon("ssh"))
        self.ssh_launcher_button.clicked.connect(self.open_ssh_launcher)
        self.action_layout.addWidget(self.ssh_launcher_button)

        self.logs_dir_button = QtWidgets.QPushButton("Logs Directory")
        self.logs_dir_button.setFixedSize(150, 30)
        self.logs_dir_button.setIcon(self._get_icon("opened-folder"))
        self.logs_dir_button.clicked.connect(lambda: self.open_path(self.output_dir))
        self.action_layout.addWidget(self.logs_dir_button)

        self.action_layout.addStretch()
        self.layout.addLayout(self.action_layout)

    def _init_tabs(self):
        """Initialize the tab widget to hold terminals."""
        self.tabs = QtWidgets.QTabWidget(parent=self)
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.layout.addWidget(self.tabs)

    def _init_command_bar(self):
        """Initialize the global command send bar."""
        self.send_command_layout = QtWidgets.QHBoxLayout()
        self.send_command_layout.setContentsMargins(0, 0, 0, 0)
        self.send_command_layout.setSpacing(5)

        self.command_line_edit = QtWidgets.QLineEdit(self)
        self.command_line_edit.setPlaceholderText("Enter command to send to all terminals")
        self.command_line_edit.returnPressed.connect(self.send_command_to_all)
        self.send_command_layout.addWidget(self.command_line_edit)

        self.send_button = QtWidgets.QPushButton("Send")
        self.send_button.setFixedSize(150, 30)
        self.send_button.setIcon(self._get_icon("send"))
        self.send_button.clicked.connect(self.send_command_to_all)
        self.send_command_layout.addWidget(self.send_button)

        self.layout.addLayout(self.send_command_layout)

    def _get_icon(self, filename):
        """Load an icon from the assets directory."""
        icon_path = os.path.join(os.path.dirname(__file__), "assets", f"{filename}.ico")
        icon = QtGui.QIcon()
        icon.addPixmap(QtGui.QPixmap(icon_path), QtGui.QIcon.Mode.Normal, QtGui.QIcon.State.Off)
        return icon

    def send_command_to_all(self):
        """
        Sends the command in the line edit to all active SpawnTerminal tabs.
        """
        command = self.command_line_edit.text().strip()
        if not command:
            return

        logger.info(f"Sending command to all terminals: {command}")

        for i in range(self.tabs.count()):
            terminal = self.tabs.widget(i)
            if hasattr(terminal, "send_command"):
                terminal.send_command(command)

        self.command_line_edit.clear()

    def open_ssh_launcher(self):
        """
        Open a dialog to paste multiple hostnames/IPs to launch SSH terminals.
        """
        logger.info("Opening SSH launcher dialog")
        self.ssh_dialog = QtWidgets.QDialog(self)
        self.ssh_dialog.setWindowTitle("SSH Launcher")
        self.ssh_dialog.setWindowFlags(
            self.ssh_dialog.windowFlags() ^ QtCore.Qt.WindowType.WindowContextHelpButtonHint
        )
        self.ssh_dialog.setWindowIcon(self._get_icon('ssh'))

        layout = QtWidgets.QVBoxLayout(self.ssh_dialog)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(5)

        self.device_text_edit = QtWidgets.QTextEdit(self.ssh_dialog)
        self.device_text_edit.setMinimumSize(QtCore.QSize(250, 500))
        layout.addWidget(self.device_text_edit)

        launch_button = QtWidgets.QPushButton("Launch", parent=self.ssh_dialog)
        launch_button.setFixedSize(150, 30)
        launch_button.clicked.connect(self.add_ssh_terminals)
        layout.addWidget(launch_button)

        self.ssh_dialog.show()

    def add_ssh_terminals(self):
        """
        Create and connect SSH terminals based on the list of hostnames in the SSH dialog.
        """
        device_list = list(filter(None, self.device_text_edit.toPlainText().splitlines()))
        if not device_list:
            logger.warning("No devices entered in SSH launcher")
            return

        logger.info(f"Launching SSH for {len(device_list)} device(s)")
        self.ssh_dialog.accept()

        for device in device_list:
            terminal = self.add_new_terminal(f"SSH: {device}", log=False)
            terminal.open_ssh(
                hostname=device,
                username=self.session.get('NETWORK_USERNAME', ''),
                password=self.session.get('NETWORK_PASSWORD', ''),
                jumphost_hostname=self.session.get('JUMPHOST_IP'),
                jumphost_username=self.session.get('JUMPHOST_USERNAME'),
                jumphost_password=self.session.get('JUMPHOST_PASSWORD')
            )

    def add_new_terminal(self, name="cmd.exe", log=True):

        terminal = SpawnTerminal(500, 500, form=self, font_size=8)
        terminal.terminated.connect(lambda: self.close_tab(index))
        index = self.tabs.addTab(terminal, name)
        self.tabs.setCurrentIndex(index)

        os.makedirs(self.output_dir, exist_ok=True)
        if log:
            terminal.start_logging(label=name, file_path=self.output_dir)

        return terminal
    
    def close_tab(self, index):
        """
        Close and clean up a terminal tab.

        Args:
            index (int): The index of the tab to close.
        """
        widget = self.tabs.widget(index)
        if widget:
            logger.info(f"Closing terminal tab at index {index}")
            widget.io.terminate()
            widget.deleteLater()
        self.tabs.removeTab(index)


    def open_path(self, path):
        """
        Open a file or directory using the system's default handler.

        Args:
            path (str): File or directory path to open.
        """
        try:
            if path and os.path.exists(path):
                logger.info(f"Opening path: {path}")
                QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))
            else:
                logger.error(f"Invalid or non-existent path: {path}")
        except Exception as e:
            logger.exception(f"Failed to open path: {e}")