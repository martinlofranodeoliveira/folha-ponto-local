"""Erros públicos com códigos estáveis e mensagens em português sem vazar dados da entrada."""


class TimesheetError(ValueError):
    def __init__(self, code: str, message: str, path: str = "") -> None:
        self.code = code
        self.message = message
        self.path = path
        super().__init__(message)
