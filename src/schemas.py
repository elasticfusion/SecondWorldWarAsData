"""Pydantic schemas for entity extraction matching spec requirements."""

from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field
from ulid import new as new_ulid


def generate_ulid() -> str:
    """Generate a new ULID string."""
    return str(new_ulid())


class SubEventFulltext(BaseModel):
    """Fulltext with absolute paragraph numbering."""

    paragraphs: Dict[str, str] = Field(
        default_factory=dict, description="Paragraph_N: text mapping"
    )


class SubEvent(BaseModel):
    """Sub-event within an event."""

    sub_event_id: str = Field(default_factory=generate_ulid, alias="Sub-eventID")
    sub_event_summary: str = Field(alias="Sub-event_summary")
    sub_event_fulltext: Dict[str, str] = Field(
        alias="Sub-event_fulltext", description="Paragraph_N: text mapping"
    )
    sub_event_images: List[List[str]] = Field(
        default_factory=list, alias="Sub-Event-Images"
    )
    sub_events_maps: List[List[str]] = Field(
        default_factory=list, alias="Sub-Events-Maps"
    )
    sub_event_dates: List[str] = Field(default_factory=list, alias="Sub-Event-Dates")
    sub_event_places: List[str] = Field(default_factory=list, alias="Sub-Event-Places")
    endnote_references: List[int] = Field(
        default_factory=list, alias="Endnote_References"
    )
    footnote_references: List[int] = Field(
        default_factory=list, alias="Footnote_References"
    )

    model_config = ConfigDict(populate_by_name=True)


class Event(BaseModel):
    """Event with ULID and sub-events."""

    event_id: str = Field(default_factory=generate_ulid, alias="EventID")
    sub_events: List[SubEvent] = Field(default_factory=list, alias="Sub-events")

    model_config = ConfigDict(populate_by_name=True)


class EventOutput(BaseModel):
    """Event extraction output matching spec format."""

    chapter: str = Field(alias="Chapter")
    event: Event = Field(alias="Event")

    model_config = ConfigDict(populate_by_name=True)


class DateMention(BaseModel):
    """Date mention with context."""

    date_mention_id: str = Field(default_factory=generate_ulid, alias="DateMentionID")
    date_start: Optional[str] = None
    date_end: Optional[str] = None
    time_start: Optional[str] = None
    time_end: Optional[str] = None
    time_precision: Optional[str] = None
    time_source: Optional[str] = None
    original_text: str

    model_config = ConfigDict(populate_by_name=True)


class DateOutput(BaseModel):
    """Date mentions output."""

    event_name: str = Field(alias="Event_Name")
    event_id: str = Field(alias="EventID")
    sub_event_name: str = Field(alias="Sub-event_Name")
    sub_event_id: str = Field(alias="Sub-eventID")
    date_mentions: List[DateMention] = Field(
        default_factory=list, alias="Date_Mentions"
    )

    model_config = ConfigDict(populate_by_name=True)


class RoutePoint(BaseModel):
    """Single point in a route."""

    sequence: int
    current_name: str
    historical_name: str
    latitude: float
    longitude: float
    bounding_box: Dict[str, float]
    geography_type: str

    model_config = ConfigDict(populate_by_name=True)


class PlaceMention(BaseModel):
    """Place mention with geocoding."""

    place_mention_id: str = Field(default_factory=generate_ulid, alias="PlaceMentionID")
    current_name: Optional[str] = None
    historical_name: Optional[str] = None
    source_language: str = "English"
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    bounding_box: Optional[Dict[str, float]] = None
    geography_type: Optional[str] = None
    route: Optional[List[RoutePoint]] = None
    date_context: Optional[str] = None
    original_text: str

    model_config = ConfigDict(populate_by_name=True)


class PlaceOutput(BaseModel):
    """Place mentions output."""

    event_name: str = Field(alias="Event_Name")
    event_id: str = Field(alias="EventID")
    sub_event_name: str = Field(alias="Sub-event_Name")
    sub_event_id: str = Field(alias="Sub-eventID")
    place_mentions: List[PlaceMention] = Field(
        default_factory=list, alias="Place_Mentions"
    )

    model_config = ConfigDict(populate_by_name=True)


class ImageReference(BaseModel):
    """Image reference in weather mention."""

    image_id: str = Field(alias="ImageID")
    description: str

    model_config = ConfigDict(populate_by_name=True)


class WeatherMention(BaseModel):
    """Weather mention with impact."""

    weather_mention_id: str = Field(
        default_factory=generate_ulid, alias="WeatherMentionID"
    )
    place_name: str
    place_mention_id: str = Field(alias="PlaceMentionID")
    date: str
    date_mention_id: str = Field(alias="DateMentionID")
    weather_description: str
    temperature: Optional[float] = None
    temperature_unit: Optional[str] = None
    measurement_system: Optional[str] = None
    notable_impact: Optional[str] = None
    api_source: Optional[str] = None
    image_references: List[ImageReference] = Field(default_factory=list)
    original_text: str

    model_config = ConfigDict(populate_by_name=True)


class WeatherOutput(BaseModel):
    """Weather mentions output."""

    event_name: str = Field(alias="Event_Name")
    event_id: str = Field(alias="EventID")
    sub_event_name: str = Field(alias="Sub-event_Name")
    sub_event_id: str = Field(alias="Sub-eventID")
    weather_mentions: List[WeatherMention] = Field(
        default_factory=list, alias="Weather_Mentions"
    )

    model_config = ConfigDict(populate_by_name=True)


class PersonEventMention(BaseModel):
    """Person's involvement in a specific event."""

    mention_id: str = Field(default_factory=generate_ulid, alias="MentionID")
    event_name: str = Field(alias="Event_Name")
    event_id: str = Field(alias="EventID")
    sub_event_name: str = Field(alias="Sub-event_Name")
    sub_event_id: str = Field(alias="Sub-eventID")
    date: Optional[str] = None
    date_mention_id: Optional[str] = Field(None, alias="DateMentionID")
    position_at_event: Optional[str] = None
    life_event: Optional[str] = None
    original_text: str

    model_config = ConfigDict(populate_by_name=True)


class MilitaryAward(BaseModel):
    """Military award details."""

    award: str
    award_class: Optional[str] = Field(None, alias="class")
    date_awarded: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class BiographicalProfile(BaseModel):
    """Person's biographical information."""

    birth_date: Optional[str] = None
    birth_place: Optional[str] = None
    death_date: Optional[str] = None
    death_place: Optional[str] = None
    nationality: Optional[str] = None
    role_type: Optional[str] = None
    military_awards: List[MilitaryAward] = Field(default_factory=list)
    biographical_details: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class Person(BaseModel):
    """Person entity with biographical profile and event mentions."""

    person_id: str = Field(default_factory=generate_ulid, alias="PersonID")
    name: str
    source_language: str = "English"
    biographical_profile: BiographicalProfile
    event_mentions: List[PersonEventMention] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class PeopleOutput(BaseModel):
    """People mentions output (centrally managed)."""

    people: List[Person] = Field(default_factory=list, alias="People")

    model_config = ConfigDict(populate_by_name=True)


class PeopleGroupEventMention(BaseModel):
    """People group's involvement in a specific event."""

    mention_id: str = Field(default_factory=generate_ulid, alias="MentionID")
    event_name: str = Field(alias="Event_Name")
    event_id: str = Field(alias="EventID")
    sub_event_name: str = Field(alias="Sub-event_Name")
    sub_event_id: str = Field(alias="Sub-eventID")
    date: Optional[str] = None
    date_mention_id: Optional[str] = Field(None, alias="DateMentionID")
    context: str
    original_text: str

    model_config = ConfigDict(populate_by_name=True)


class PeopleGroup(BaseModel):
    """People group entity (country, military unit, alliance, etc.)."""

    group_id: str = Field(default_factory=generate_ulid, alias="GroupID")
    group_name: str
    group_type: str
    country_of_origin: Optional[str] = Field(
        default=None,
        description="ISO 3166-1 alpha-3 country code (e.g., 'USA', 'GBR', 'DEU', 'FRA', 'ITA', 'JPN')",
    )
    alliance_membership: Optional[List[str]] = None
    source_language: str = "English"
    description: str
    military_hierarchy: Optional[str] = None
    parent_organization: Optional[str] = None
    member_countries: Optional[List[str]] = None
    common_name: Optional[str] = None
    event_mentions: List[PeopleGroupEventMention] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class PeopleGroupOutput(BaseModel):
    """People groups output (centrally managed)."""

    people_groups: List[PeopleGroup] = Field(
        default_factory=list, alias="People_Groups"
    )

    model_config = ConfigDict(populate_by_name=True)
