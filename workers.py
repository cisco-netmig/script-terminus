import logging
logger = logging.getLogger(__name__)

import re
from PyQt5 import QtCore

class CommandThread(QtCore.QThread):
    """Thread to run a command on the terminal and wait for expected output."""
    finished = QtCore.pyqtSignal(bytes)
    error = QtCore.pyqtSignal(str)

    def __init__(self, terminal, command: str, expect: bytes, timeout: int):
        super().__init__()
        self.terminal = terminal
        self.command = command
        self.expect = expect
        self.timeout = timeout

    def run(self):
        try:
            logger.info(f"Executing command: {self.command}")
            result = self.terminal._send_command_blocking(self.command, self.expect, self.timeout)
            self.finished.emit(result)
        except Exception as e:
            logger.error(f"Error while executing command: {e}")
            self.error.emit(str(e))

class SSHThread(QtCore.QThread):
    finished = QtCore.pyqtSignal(bytes)
    error = QtCore.pyqtSignal(str)

    def __init__(self,
                 terminal,
                 hostname,
                 username,
                 password,
                 jumphost_hostname,
                 jumphost_username,
                 jumphost_password,
                 timeout):
        super().__init__()
        self.terminal = terminal
        self.hostname = hostname
        self.username = username
        self.password = password
        self.jumphost_hostname = jumphost_hostname
        self.jumphost_username = jumphost_username
        self.jumphost_password = jumphost_password
        self.timeout = timeout


    def run(self):
        try:
            logger.info(f"Starting SSH to {self.hostname} (via jumphost: {self.jumphost_hostname})")
            result = b""

            if self.jumphost_hostname:
                result += self.terminal._send_command_blocking(
                    f"ssh -o StrictHostKeyChecking=no {self.jumphost_username}@{self.jumphost_hostname}",
                    rb"[Pp]assword:",
                    self.timeout
                )
                result += self.terminal._send_command_blocking(self.jumphost_password, self.terminal.BASE_PROMPT_REGEX, self.timeout)

            result += self.terminal._send_command_blocking(
                f"ssh -o StrictHostKeyChecking=no {self.username}@{self.hostname}",
                rb"[Pp]assword:",
                self.timeout
            )
            result += self.terminal._send_command_blocking(self.password, self.terminal.BASE_PROMPT_REGEX, self.timeout)

            cleaned = self.terminal.ANSI_ESCAPE_REGEX.sub(b"", result)
            match = re.search(rb"(\S+)[>#\$]\s*$", cleaned)
            prompt_name = match.group(1).decode() if match else hostname

            self.finished.emit(prompt_name.encode())

        except Exception as e:
            logger.error(f"SSH connection failed: {e}")
            self.error.emit(str(e))