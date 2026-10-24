"""Parse rekordbox collection XML (AverageBpm, TEMPO, Tonality, POSITION_MARK)."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import unquote, urlparse


def location_to_path(location: str) -> str:
    """Convert file://localhost/... Location to a filesystem path."""
    if not location:
        return ""
    loc = unquote(location)
    if loc.startswith("file://localhost/"):
        path = loc[len("file://localhost/") :]
    elif loc.startswith("file://"):
        parsed = urlparse(loc)
        path = unquote(parsed.path or "")
        if path.startswith("/") and len(path) > 2 and path[2] == ":":
            # Windows file:///C:/...
            path = path[1:]
    else:
        path = loc
    # Windows drive paths may arrive as /C:/...
    if len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    return path.replace("/", "\\") if (len(path) >= 2 and path[1] == ":") else path


def parse_collection(xml_path: str) -> list[dict[str, Any]]:
    """Parse DJ_PLAYLISTS/COLLECTION/TRACK entries."""
    tree = ET.parse(xml_path)
    root = tree.getroot()
    collection = root.find("COLLECTION")
    if collection is None:
        return []
    out: list[dict[str, Any]] = []
    for track in collection.findall("TRACK"):
        attrs = dict(track.attrib)
        tempos = []
        for tempo in track.findall("TEMPO"):
            tempos.append(dict(tempo.attrib))
        marks = []
        for mark in track.findall("POSITION_MARK"):
            marks.append(dict(mark.attrib))
        attrs["tempos"] = tempos
        attrs["marks"] = marks
        out.append(attrs)
    return out
