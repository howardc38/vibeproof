class SecretString:
    def __init__(self, value: str) -> None:
        self._value = value

    def __str__(self) -> str:
        return "<redacted:SecretString>"

    def __bool__(self) -> bool:
        return bool(self._value)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, SecretString):
            return self._value == other._value
        return False

    def __hash__(self) -> int:
        return hash(self._value)
