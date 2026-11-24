class BusinessError(Exception):
    def __init__(self, message, status=422, details=None):
        super().__init__(message)
        self.status = status
        self.details = details or {}
