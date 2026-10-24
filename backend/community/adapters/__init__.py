"""Chat platform adapters."""

from community.adapters.base import ChatAdapter
from community.adapters.facebook import FacebookAdapter
from community.adapters.kick import KickAdapter, map_kick_event
from community.adapters.rumble import RumbleAdapter
from community.adapters.tiktok import map_tiktok_event
from community.adapters.twitch import TwitchAdapter
from community.adapters.youtube import YouTubeAdapter
from community.adapters.youtube_official import YouTubeOfficialAdapter

__all__ = [
    "ChatAdapter",
    "YouTubeAdapter",
    "YouTubeOfficialAdapter",
    "TwitchAdapter",
    "KickAdapter",
    "FacebookAdapter",
    "RumbleAdapter",
    "map_kick_event",
    "map_tiktok_event",
]
