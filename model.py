from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional


# ============================================================
# World model
# ============================================================

@dataclass
class Entity:
    eid: str
    name: str
    aliases: List[str] = field(default_factory=list)
    tags: set[str] = field(default_factory=set)
    props: Dict[str, Any] = field(default_factory=dict)
    location: Optional[str] = None
    contains: List[str] = field(default_factory=list)

    def all_names(self) -> List[str]:
        return [self.name.lower(), *[a.lower() for a in self.aliases]]


@dataclass
class Room:
    rid: str
    title: str
    desc: str
    exits: Dict[str, str] = field(default_factory=dict)
    entities: List[str] = field(default_factory=list)


@dataclass
class Player:
    location: str
    inventory: List[str] = field(default_factory=list)


@dataclass
class Clock:
    now: int = 0

    def advance(self, dt: int = 1) -> None:
        self.now += dt


@dataclass
class World:
    rooms: Dict[str, Room]
    entities: Dict[str, Entity]
    player: Player
    clock: Clock = field(default_factory=Clock)
    last_referred: List[str] = field(default_factory=list)

    def room(self) -> Room:
        return self.rooms[self.player.location]


    def entity(self, eid: str) -> Entity:
        return self.entities[eid]


    def visible_entities(self) -> List[str]:
        """
        Visible = things in the room + things in inventory + contents of open
        containers in the room + doors that connect to the current room.
        """
        vis = list(self.room().entities) + list(self.player.inventory)

        # Add contents of open containers in the room.
        for eid in list(self.room().entities):
            ent = self.entity(eid)
            if "container" in ent.tags and ent.props.get("open", False):
                vis.extend(ent.contains)

        # Add connector doors that touch the current room.
        for eid, ent in self.entities.items():
            if "door" in ent.tags:
                room_a = ent.props.get("room_a")
                room_b = ent.props.get("room_b")
                if self.player.location == room_a or self.player.location == room_b:
                    if eid not in vis:
                        vis.append(eid)

        return vis


    def note_ref(self, eids: Iterable[str]) -> None:
        """Update discourse memory for pronoun resolution."""
        for eid in eids:
            if eid in self.last_referred:
                self.last_referred.remove(eid)
            self.last_referred.insert(0, eid)
        self.last_referred = self.last_referred[:5]