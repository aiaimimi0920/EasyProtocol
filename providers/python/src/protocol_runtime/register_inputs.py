from __future__ import annotations

import os
import random


def generate_name() -> tuple[str, str]:
    first = [
        "Neo", "John", "Sarah", "Michael", "Emma", "David", "James", "Robert", "Mary", "William",
        "Richard", "Thomas", "Charles", "Christopher", "Daniel", "Matthew", "Anthony", "Mark", "Donald", "Steven",
        "Paul", "Andrew", "Joshua", "Kenneth", "Kevin", "Brian", "George", "Edward", "Ronald", "Timothy",
    ]
    last = [
        "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez",
        "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin",
        "Lee", "Perez", "Thompson", "White",
    ]
    return random.choice(first), random.choice(last)


def generate_pwd(length: int = 12) -> str:
    fixed = str(
        os.environ.get("PROTOCOL_FIXED_PASSWORD")
        or os.environ.get("DEBUG_FIXED_REGISTER_PASSWORD")
        or ""
    ).strip()
    if fixed:
        return fixed
    chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@*&"
    return "".join(random.choice(chars) for _ in range(length)) + "A1@"
