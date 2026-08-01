"""Sample Python module for code_index tests."""

from pathlib import Path


class Greeter:
    """A simple greeter class."""

    def greet(self, name: str) -> str:
        """Return a greeting."""
        return f"Hello, {name}!"


def main() -> None:
    """Entry point."""
    g = Greeter()
    print(g.greet("World"))
