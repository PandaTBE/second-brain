"""Errors whose messages are safe to send to the browser."""


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def error_content(error: AppError) -> dict:
    return {"success": False, "error": {"code": error.code, "message": error.message}}
