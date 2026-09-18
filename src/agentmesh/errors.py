class AgentMeshError(Exception):
    """A safe, user-facing domain error."""

    def __init__(self, message: str, status: int = 400, code: str = "bad_request"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


class NotFound(AgentMeshError):
    def __init__(self, message: str = "Not found"):
        super().__init__(message, 404, "not_found")


class Forbidden(AgentMeshError):
    def __init__(self, message: str = "Forbidden"):
        super().__init__(message, 403, "forbidden")


class Unauthorized(AgentMeshError):
    def __init__(self, message: str = "Authentication required"):
        super().__init__(message, 401, "unauthorized")


class Conflict(AgentMeshError):
    def __init__(self, message: str = "Conflict"):
        super().__init__(message, 409, "conflict")

