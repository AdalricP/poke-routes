from typing import Optional

DEFAULT_ROUTE_WEATHER = "WEATHER_SUNNY"

EMERALD_WEATHER_FOR_EFFECT = {
    "snow": "WEATHER_SNOW",
    "rain": "WEATHER_RAIN",
    "ash": "WEATHER_VOLCANIC_ASH",
}

NOTE_FOR_EFFECT_WITHOUT_EMERALD_WEATHER = {
    "leaves": "falling leaves are a preview-only effect: Emerald has no leaf weather, so the route keeps its spec weather",
}


def effect_of_route(route_spec) -> str:
    route_effect = getattr(route_spec, "effect", "none")
    return route_effect


def is_weather_set_by_hand(route_spec) -> bool:
    is_weather_changed_from_default = route_spec.weather != DEFAULT_ROUTE_WEATHER
    return is_weather_changed_from_default


def emerald_weather_for_route(route_spec) -> str:
    effect_weather = EMERALD_WEATHER_FOR_EFFECT.get(effect_of_route(route_spec))
    if effect_weather is None or is_weather_set_by_hand(route_spec):
        return route_spec.weather
    return effect_weather


def effect_export_note(route_spec) -> Optional[str]:
    if effect_of_route(route_spec) in NOTE_FOR_EFFECT_WITHOUT_EMERALD_WEATHER:
        return NOTE_FOR_EFFECT_WITHOUT_EMERALD_WEATHER[effect_of_route(route_spec)]
    effect_weather = EMERALD_WEATHER_FOR_EFFECT.get(effect_of_route(route_spec))
    if effect_weather is None or not is_weather_set_by_hand(route_spec):
        return None
    note_about_kept_weather = f"effect {effect_of_route(route_spec)} would be {effect_weather}, but the spec sets {route_spec.weather} by hand, so that is kept"
    return note_about_kept_weather


def effect_summary_for_route(route_spec) -> dict:
    effect_summary = {"effect": effect_of_route(route_spec), "weather": emerald_weather_for_route(route_spec)}
    export_note = effect_export_note(route_spec)
    if export_note:
        effect_summary["effect_note"] = export_note
    return effect_summary
