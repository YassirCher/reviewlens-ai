"""Small, extensible vocabulary for presenting source-grounded product facts.

The analyst may propose any fact. Only unambiguous labels receive a canonical
identity; unfamiliar labels remain visible with their original evidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ProductAttribute:
    key: str
    group: str
    label: str
    aliases: tuple[str, ...]


# These are presentation identities, not a list of facts the analyst must fill.
# Keep aliases narrow: a generic "battery" or "driver" can name several parts.
ATTRIBUTE_CATALOG = (
    ProductAttribute("audio.driver_size", "Audio", "Driver size", ("driver size", "driver diameter", "speaker driver size")),
    ProductAttribute("audio.driver_type", "Audio", "Driver type", ("driver type", "driver technology")),
    ProductAttribute("audio.impedance", "Audio", "Impedance", ("impedance",)),
    ProductAttribute("battery.case_capacity", "Battery", "Charging case capacity", ("charging case capacity", "case battery capacity")),
    ProductAttribute("battery.capacity", "Battery", "Battery capacity", ("battery capacity",)),
    ProductAttribute("physical.earbud_weight", "Physical", "Earbud weight", ("earbud weight", "earpiece weight")),
    ProductAttribute("physical.earbud_dimensions", "Physical", "Earbud dimensions", ("earbud dimensions", "earpiece dimensions")),
    ProductAttribute("physical.weight", "Physical", "Weight", ("weight",)),
    ProductAttribute("physical.dimensions", "Physical", "Dimensions", ("dimensions",)),
    ProductAttribute("connectivity.bluetooth_version", "Connectivity", "Bluetooth version", ("bluetooth version",)),
    ProductAttribute("durability.water_resistance", "Durability", "Water resistance rating", ("water resistance rating", "ip rating")),
    ProductAttribute("display.size", "Display", "Display size", ("display size", "screen size")),
    ProductAttribute("display.resolution", "Display", "Display resolution", ("display resolution", "screen resolution")),
    ProductAttribute("display.panel_type", "Display", "Panel type", ("panel type", "display panel type")),
    ProductAttribute("display.brightness", "Display", "Peak brightness", ("peak brightness", "display peak brightness")),
    ProductAttribute("display.refresh_rate", "Display", "Refresh rate", ("refresh rate", "screen refresh rate")),
    ProductAttribute("compute.processor_model", "Processing", "Processor model", ("processor model", "cpu model")),
    ProductAttribute("compute.graphics_model", "Processing", "Graphics model", ("graphics model", "gpu model")),
    ProductAttribute("compute.memory_capacity", "Processing", "Memory capacity", ("memory capacity", "ram capacity")),
    ProductAttribute("compute.storage_capacity", "Processing", "Storage capacity", ("storage capacity",)),
)

_BY_ALIAS = {alias: attribute for attribute in ATTRIBUTE_CATALOG for alias in attribute.aliases}
_MILLIMETRES = re.compile(r"^\d+(?:[.,]\d+)?\s*mm$", re.IGNORECASE)
_ACRONYMS = {"anc": "ANC", "enc": "ENC", "ip": "IP", "oled": "OLED", "usb": "USB", "wi-fi": "Wi-Fi"}


def _words(value: str) -> str:
    return " ".join(re.sub(r"[_-]+", " ", value).casefold().split())


def _readable(value: str) -> str:
    cleaned = " ".join(value.split())
    if "_" not in cleaned and not cleaned.islower():
        return cleaned
    words = cleaned.replace("_", " ").split()
    return " ".join(_ACRONYMS.get(word.casefold(), word.capitalize() if index == 0 else word.casefold())
                    for index, word in enumerate(words))


def canonical_fact(group: str, label: str, value: str) -> tuple[str, str, str]:
    """Return stable identity and public group/label without changing the value."""
    group_key, label_key = _words(group), _words(label)
    attribute = _BY_ALIAS.get(label_key)
    if label_key == "driver" and _MILLIMETRES.fullmatch(value.strip()):
        attribute = _BY_ALIAS["driver size"]
    elif label_key == "weight" and "earbud" in group_key:
        attribute = _BY_ALIAS["earbud weight"]
    elif label_key == "dimensions" and "earbud" in group_key:
        attribute = _BY_ALIAS["earbud dimensions"]
    elif label_key == "case capacity" and group_key in {"battery", "power", "charging case"}:
        attribute = _BY_ALIAS["charging case capacity"]
    elif label_key in {"processor", "processor name", "chipset name", "soc", "soc name"} \
            and group_key in {"chip", "chipset", "processor", "soc"}:
        attribute = _BY_ALIAS["processor model"]
    if attribute is not None:
        return attribute.key, attribute.group, attribute.label
    return f"custom:{group_key}:{label_key}", _readable(group), _readable(label)


def canonical_variant_dimension(dimension: str) -> str:
    key = _words(dimension)
    if key in {"color", "colour"}:
        return "Color"
    return _readable(dimension)
