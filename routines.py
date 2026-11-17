"""Named, local command lists; never executable code or stored screen captures."""
import json
from pathlib import Path
import re


ROUTINES_PATH = Path(__file__).resolve().with_name("routines.json")


def routine_name(name):
    name = name.strip().strip('"').strip()
    if not re.fullmatch(r"[\w][\w -]{0,59}", name):
        raise ValueError("Use a routine name of 1–60 letters, numbers, spaces, or hyphens.")
    return name.casefold()


class RoutineStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else ROUTINES_PATH

    def read(self):
        if not self.path.exists():
            return {}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or len(data) > 100:
            raise ValueError("Routine storage is invalid; it has not been overwritten.")
        return data

    def write(self, data):
        temporary = self.path.with_suffix(".json.tmp")
        try:
            temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def save(self, name, steps):
        key = routine_name(name)
        data = self.read()
        if key not in data and len(data) >= 100:
            raise ValueError("Routine storage is full (100 routines).")
        data[key] = steps
        self.write(data)

    def load(self, name):
        key = routine_name(name)
        data = self.read()
        if key not in data:
            raise ValueError(f"No routine named '{key}'. Say 'list routines'.")
        return data[key]

    def delete(self, name):
        key = routine_name(name)
        data = self.read()
        if key not in data:
            raise ValueError(f"No routine named '{key}'.")
        del data[key]
        self.write(data)
