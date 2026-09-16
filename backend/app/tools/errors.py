class ToolExecutionError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        category: str = "internal",
        retryable: bool = False,
        safe_metadata: dict | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.category = category
        self.retryable = retryable
        self.safe_metadata = safe_metadata or {}


class ToolAuthorizationError(ToolExecutionError):
    def __init__(self, code: str = "tool_not_authorized") -> None:
        super().__init__(code, category="authorization", retryable=False)
