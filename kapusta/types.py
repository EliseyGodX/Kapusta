"""
This module defines type aliases for use in the kapusta project.

Type Aliases:
    Seconds (int): An alias for representing seconds as an integer.
    TaskId (int): Task ID.
    Sentinel (Any): A unique object used to indicate an empty value in function
        arguments, to avoid using Optional[...] = None. It should be checked
        with `x is Sentinel` (similar to `x is None`).
"""

from typing import Any, TypeAlias

Seconds: TypeAlias = int
TaskId: TypeAlias = int
Sentinel: Any = object
