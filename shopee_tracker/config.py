"""Load shops.yaml (hoặc .json) cho multi-shop tracking."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG = Path("shops.yaml")


@dataclass
class ShopEntry:
    url: str
    alias: str | None = None
    note: str | None = None
    limit: int = 100
    full: bool = False
    engine: str = "curl"


def load_shops(path: Path = DEFAULT_CONFIG) -> list[ShopEntry]:
    if not path.exists():
        raise FileNotFoundError(f"Config file không tồn tại: {path}")
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".yaml", ".yml"):
        try:
            import yaml
            data = yaml.safe_load(text)
        except ImportError as e:
            raise ImportError("Cần PyYAML: pip install PyYAML") from e
    else:
        data = json.loads(text)

    if not isinstance(data, dict) or "shops" not in data:
        raise ValueError("Config phải có key 'shops' là danh sách")

    entries: list[ShopEntry] = []
    for item in data["shops"]:
        if isinstance(item, str):
            entries.append(ShopEntry(url=item))
        elif isinstance(item, dict):
            if "url" not in item:
                raise ValueError(f"Mỗi entry phải có 'url': {item}")
            entries.append(
                ShopEntry(
                    url=item["url"],
                    alias=item.get("alias"),
                    note=item.get("note"),
                    limit=int(item.get("limit", 100)),
                    full=bool(item.get("full", False)),
                    engine=item.get("engine", "curl"),
                )
            )
        else:
            raise ValueError(f"Entry không hợp lệ: {item}")
    return entries
