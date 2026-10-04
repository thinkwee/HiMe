"""Feature mapping between open-wearables' ``SeriesType`` catalogue and HiMe's
native feature space (see ``backend/data_readers/apple_health_features.py``).

Two lookup tables:

``SERIES_TYPE_MAP``
    OW series type -> existing HiMe ``feature_type``, for metrics HiMe
    already tracks from Apple Watch. Reused verbatim (unit + aggregation
    stay whatever ``FEATURE_SPEC`` already says) so a Garmin heart-rate
    sample lands on the exact same dashboard card as an Apple Watch one.
    Where OW's and HiMe's storage units differ, ``SERIES_TYPE_CONVERSION``
    gives the multiplier applied to the OW value before it is stored.

``OW_FEATURE_SPEC``
    Display/aggregation specs (same schema as ``FEATURE_SPEC``) for OW
    metrics HiMe has no native equivalent for. Keyed by the OW series type
    string itself, so there is never a collision with a
    ``SERIES_TYPE_MAP`` target.

A type absent from *both* tables is intentionally unmapped (see
``physical_effort`` below) and is skipped + debug-logged by the mapper
rather than guessed at.
"""
from ...config import settings
from ...data_readers.apple_health_features import AGG_MEAN, AGG_SUM

# ---------------------------------------------------------------------------
# OW series type -> HiMe feature_type (metrics HiMe already tracks)
# ---------------------------------------------------------------------------
#
# NOTE on units: OW's SERIES_TYPE_DEFINITIONS uses "public" units (bpm,
# percent 0-100, cm, minutes, ...). Several of HiMe's *storage* units differ
# from its *display* units (see FEATURE_SPEC's `display_scale`) — e.g.
# blood_oxygen is stored as a 0-1 fraction, not a 0-100 percent. Where the
# storage units differ, SERIES_TYPE_CONVERSION carries the multiplier.
SERIES_TYPE_MAP: dict[str, str] = {
    # Heart & cardiovascular
    "heart_rate": "heart_rate",
    "resting_heart_rate": "resting_heart_rate",
    "heart_rate_variability_sdnn": "heart_rate_variability",
    "heart_rate_recovery_one_minute": "heart_rate_recovery",
    "walking_heart_rate_average": "walking_heart_rate_avg",
    # NOTE: heart_rate_variability_rmssd is intentionally NOT mapped to
    # HiMe's "heart_rate_variability" — that feature is SDNN-only upstream
    # (Apple HealthKit). Mixing RMSSD samples into the same series would
    # silently corrupt its meaning. It gets its own OW_FEATURE_SPEC entry.

    # Blood & respiratory
    "oxygen_saturation": "blood_oxygen",  # unit conversion: percent -> fraction
    "respiratory_rate": "respiratory_rate",
    "sleeping_breathing_disturbances": "sleeping_breathing_disturbances",

    # Body composition / fitness
    "height": "height",
    "weight": "body_mass",
    "body_mass_index": "body_mass_index",
    "vo2_max": "vo2max",
    "six_minute_walk_test_distance": "six_minute_walk",
    # skin_temperature_deviation (nightly baseline-relative wrist/skin temp)
    # is the same physical quantity as HiMe's Apple-Watch "sleeping_wrist_temp".
    "skin_temperature_deviation": "sleeping_wrist_temp",

    # Activity
    "steps": "steps",
    "energy": "active_energy",           # OW "active energy burned"
    "basal_energy": "resting_energy",    # OW "basal energy" == Apple's resting energy
    "flights_climbed": "flights_climbed",
    "stand_time": "stand_time",          # unit conversion: minutes -> seconds
    "exercise_time": "exercise_time",    # unit conversion: minutes -> seconds
    "distance_walking_running": "distance",
    "distance_cycling": "distance_cycling",

    # Walking / gait
    "walking_speed": "walking_speed",
    "walking_step_length": "walking_step_length",              # cm -> m
    "walking_double_support_percentage": "walking_double_support",  # percent -> fraction
    "walking_asymmetry_percentage": "walking_asymmetry",        # percent -> fraction
    "walking_steadiness": "walking_steadiness",                 # percent -> fraction
    "stair_descent_speed": "stair_descent_speed",
    "stair_ascent_speed": "stair_ascent_speed",

    # Running
    "running_power": "running_power",
    "running_speed": "running_speed",
    "running_vertical_oscillation": "running_vertical_oscillation",
    "running_ground_contact_time": "running_ground_contact",
    "running_stride_length": "running_stride_length",          # cm -> m

    # Environmental audio
    "environmental_audio_exposure": "environmental_audio",
    "headphone_audio_exposure": "headphone_audio",

    # Intake — same physical quantity as HiMe's "water" (fluid volume, mL)
    "hydration": "water",

    # time_in_daylight: unit conversion: minutes -> seconds
    "time_in_daylight": "time_in_daylight",
}

# Multiplier applied to the OW value to get the value HiMe stores. Absent
# from this dict == 1.0 (no conversion; units already match).
SERIES_TYPE_CONVERSION: dict[str, float] = {
    "oxygen_saturation": 0.01,                       # percent (0-100) -> fraction (0-1)
    "stand_time": 60.0,                              # minutes -> seconds
    "exercise_time": 60.0,                           # minutes -> seconds
    "walking_step_length": 0.01,                     # cm -> m
    "walking_double_support_percentage": 0.01,       # percent -> fraction
    "walking_asymmetry_percentage": 0.01,             # percent -> fraction
    "walking_steadiness": 0.01,                       # percent -> fraction
    "running_stride_length": 0.01,                    # cm -> m
    "time_in_daylight": 60.0,                         # minutes -> seconds
}

# Series types deliberately left unmapped (name overlaps with a HiMe feature
# but the underlying quantity/unit isn't guaranteed compatible across
# providers) — kept here, and NOT in OW_FEATURE_SPEC, so the mapper skips
# and debug-logs them rather than silently guessing.
#   - physical_effort: HiMe's Apple-only feature is a HealthKit-specific MET
#     rate (kcal/hr*kg); OW normalizes provider "physical effort" as a
#     unitless 0-100 "score" (SERIES_TYPE_DEFINITIONS unit="score"). Not
#     the same quantity.
UNMAPPED_SERIES_TYPES: frozenset[str] = frozenset({"physical_effort"})


# ---------------------------------------------------------------------------
# OW_FEATURE_SPEC — OW-only metrics, same schema as apple_health_features.FEATURE_SPEC
# ---------------------------------------------------------------------------
OW_FEATURE_SPEC: dict[str, dict] = {
    # === Heart / HRV ===
    "heart_rate_variability_rmssd": {
        "raw_form": "RMSSD (ms), open-wearables only",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "ms",
        "display_unit": "ms",
        "format": "{:.0f}",
    },
    "atrial_fibrillation_burden": {
        "raw_form": "AFib burden (count/index)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "count",
        "display_unit": "",
        "format": "{:.1f}",
    },

    # === Blood / metabolic ===
    "blood_glucose": {
        "raw_form": "Blood glucose (mg/dL)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "mg/dL",
        "display_unit": "mg/dL",
        "format": "{:.0f}",
    },
    "blood_pressure_systolic": {
        "raw_form": "Systolic blood pressure (mmHg)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "mmHg",
        "display_unit": "mmHg",
        "format": "{:.0f}",
    },
    "blood_pressure_diastolic": {
        "raw_form": "Diastolic blood pressure (mmHg)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "mmHg",
        "display_unit": "mmHg",
        "format": "{:.0f}",
    },
    "blood_alcohol_content": {
        "raw_form": "Blood alcohol content (mg/dL)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "mg/dL",
        "display_unit": "mg/dL",
        "format": "{:.1f}",
    },
    "breathing_disturbance_index": {
        "raw_form": "Breathing disturbance index (score)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "score",
        "display_unit": "",
        "format": "{:.1f}",
    },
    "peripheral_perfusion_index": {
        "raw_form": "Peripheral perfusion index (score)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "score",
        "display_unit": "",
        "format": "{:.1f}",
    },
    "forced_vital_capacity": {
        "raw_form": "Forced vital capacity (L)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "L",
        "display_unit": "L",
        "format": "{:.2f}",
    },
    "forced_expiratory_volume_1": {
        "raw_form": "Forced expiratory volume in 1s (L)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "L",
        "display_unit": "L",
        "format": "{:.2f}",
    },
    "peak_expiratory_flow_rate": {
        "raw_form": "Peak expiratory flow rate (L/min)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "L/min",
        "display_unit": "L/min",
        "format": "{:.0f}",
    },

    # === Body composition / temperature ===
    "body_fat_percentage": {
        "raw_form": "Body fat percentage (0-100)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "%",
        "display_unit": "%",
        "format": "{:.1f}",
    },
    "lean_body_mass": {
        "raw_form": "Lean body mass (kg)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "kg",
        "display_unit": "kg",
        "format": "{:.1f}",
    },
    "body_fat_mass": {
        "raw_form": "Body fat mass (kg)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "kg",
        "display_unit": "kg",
        "format": "{:.1f}",
    },
    "skeletal_muscle_mass": {
        "raw_form": "Skeletal muscle mass (kg)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "kg",
        "display_unit": "kg",
        "format": "{:.1f}",
    },
    "waist_circumference": {
        "raw_form": "Waist circumference (cm)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "cm",
        "display_unit": "cm",
        "format": "{:.1f}",
    },
    "body_temperature": {
        "raw_form": "Body core temperature (degC)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "degC",
        "display_unit": "°C",
        "format": "{:.1f}",
    },
    "skin_temperature": {
        "raw_form": "Absolute skin temperature (degC), Garmin/Oura",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "degC",
        "display_unit": "°C",
        "format": "{:.1f}",
    },
    "skin_temperature_trend_deviation": {
        "raw_form": "Skin temperature trend deviation (degC)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "degC",
        "display_unit": "°C",
        "format": "{:.2f}",
    },
    "cardiovascular_age": {
        "raw_form": "Estimated cardiovascular age (years)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "years",
        "display_unit": "yrs",
        "format": "{:.0f}",
    },

    # === Activity ===
    "average_met": {
        "raw_form": "Average metabolic equivalent (MET)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "met",
        "display_unit": "MET",
        "format": "{:.1f}",
    },
    "active_time": {
        "raw_form": "Provider-reported daily active time (min)",
        "processing": "Store as-is",
        "aggregation": AGG_SUM,
        "unit": "minutes",
        "display_unit": "min",
        "format": "{:.0f}",
    },
    "distance_swimming": {
        "raw_form": "Swimming distance (m)",
        "processing": "Store as-is",
        "aggregation": AGG_SUM,
        "unit": "m",
        "display_unit": "km",
        "format": "{:.2f}",
        "display_scale": 0.001,
    },
    "distance_downhill_snow_sports": {
        "raw_form": "Downhill snow sports distance (m)",
        "processing": "Store as-is",
        "aggregation": AGG_SUM,
        "unit": "m",
        "display_unit": "km",
        "format": "{:.2f}",
        "display_scale": 0.001,
    },
    "distance_other": {
        "raw_form": "Uncategorized distance (m)",
        "processing": "Store as-is",
        "aggregation": AGG_SUM,
        "unit": "m",
        "display_unit": "km",
        "format": "{:.2f}",
        "display_scale": 0.001,
    },
    "swimming_stroke_count": {
        "raw_form": "Swimming stroke count",
        "processing": "Store as-is",
        "aggregation": AGG_SUM,
        "unit": "count",
        "display_unit": "strokes",
        "format": "{:.0f}",
    },
    "underwater_depth": {
        "raw_form": "Underwater depth (m)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "m",
        "display_unit": "m",
        "format": "{:.1f}",
    },
    "running_vertical_ratio": {
        "raw_form": "Running vertical ratio (%)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "%",
        "display_unit": "%",
        "format": "{:.1f}",
    },
    "running_stance_time_balance": {
        "raw_form": "Running stance time balance (%)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "%",
        "display_unit": "%",
        "format": "{:.1f}",
    },
    "cadence": {
        "raw_form": "Generic cadence (rpm)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "rpm",
        "display_unit": "rpm",
        "format": "{:.0f}",
    },
    "power": {
        "raw_form": "Generic power output (W)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "W",
        "display_unit": "W",
        "format": "{:.0f}",
    },
    "speed": {
        "raw_form": "Generic speed (m/s)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "m/s",
        "display_unit": "m/s",
        "format": "{:.2f}",
    },
    "workout_effort_score": {
        "raw_form": "Provider workout effort score",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "score",
        "display_unit": "",
        "format": "{:.0f}",
    },
    "estimated_workout_effort_score": {
        "raw_form": "Provider estimated workout effort score",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "score",
        "display_unit": "",
        "format": "{:.0f}",
    },

    # === Environmental ===
    "environmental_sound_reduction": {
        "raw_form": "Environmental sound reduction (dB)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "dB",
        "display_unit": "dB",
        "format": "{:.1f}",
    },
    "water_temperature": {
        "raw_form": "Water temperature (degC)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "degC",
        "display_unit": "°C",
        "format": "{:.1f}",
    },
    "uv_exposure": {
        "raw_form": "UV index exposure",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "count",
        "display_unit": "index",
        "format": "{:.1f}",
    },
    "inhaler_usage": {
        "raw_form": "Inhaler usage events",
        "processing": "value=count",
        "aggregation": AGG_SUM,
        "unit": "count",
        "display_unit": "uses",
        "format": "{:.0f}",
    },
    "weather_temperature": {
        "raw_form": "Ambient weather temperature (degC)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "degC",
        "display_unit": "°C",
        "format": "{:.1f}",
    },
    "weather_humidity": {
        "raw_form": "Ambient weather humidity (%)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "%",
        "display_unit": "%",
        "format": "{:.0f}",
    },
    "elevation": {
        "raw_form": "Elevation (m)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "m",
        "display_unit": "m",
        "format": "{:.0f}",
    },
    "latitude": {
        "raw_form": "GPS latitude (degrees)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "degrees",
        "display_unit": "°",
        "format": "{:.5f}",
    },
    "longitude": {
        "raw_form": "GPS longitude (degrees)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "degrees",
        "display_unit": "°",
        "format": "{:.5f}",
    },
    "air_temperature": {
        "raw_form": "Ambient air temperature (degC)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "degC",
        "display_unit": "°C",
        "format": "{:.1f}",
    },

    # === Garmin-specific ===
    "garmin_stress_level": {
        "raw_form": "Garmin stress score (0-100)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "score",
        "display_unit": "",
        "format": "{:.0f}",
    },
    "garmin_skin_temperature": {
        "raw_form": "Garmin skin temp deviation from baseline (degC)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "degC",
        "display_unit": "°C",
        "format": "{:.2f}",
    },
    "garmin_fitness_age": {
        "raw_form": "Garmin fitness age estimate (years)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "years",
        "display_unit": "yrs",
        "format": "{:.0f}",
    },
    "garmin_body_battery": {
        "raw_form": "Garmin body battery (0-100)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "%",
        "display_unit": "",
        "format": "{:.0f}",
        "note": "Recovery/readiness-type score. Higher is more recovered.",
    },

    # === Other ===
    "electrodermal_activity": {
        "raw_form": "Electrodermal activity (count)",
        "processing": "Store as-is",
        "aggregation": AGG_MEAN,
        "unit": "count",
        "display_unit": "",
        "format": "{:.1f}",
    },
    "push_count": {
        "raw_form": "Wheelchair push count",
        "processing": "Store as-is",
        "aggregation": AGG_SUM,
        "unit": "count",
        "display_unit": "pushes",
        "format": "{:.0f}",
    },
    "insulin_delivery": {
        "raw_form": "Insulin delivery events",
        "processing": "Store as-is",
        "aggregation": AGG_SUM,
        "unit": "count",
        "display_unit": "",
        "format": "{:.1f}",
    },
    "number_of_times_fallen": {
        "raw_form": "Fall detection events",
        "processing": "value=count",
        "aggregation": AGG_SUM,
        "unit": "count",
        "display_unit": "falls",
        "format": "{:.0f}",
    },
    "number_of_alcoholic_beverages": {
        "raw_form": "Alcoholic beverages logged",
        "processing": "Store as-is",
        "aggregation": AGG_SUM,
        "unit": "count",
        "display_unit": "drinks",
        "format": "{:.0f}",
    },
    "nike_fuel": {
        "raw_form": "NikeFuel points",
        "processing": "Store as-is",
        "aggregation": AGG_SUM,
        "unit": "count",
        "display_unit": "pts",
        "format": "{:.0f}",
    },

    # === Generic workout bucket — OW workouts whose type doesn't match one
    # of Apple's known categories (running/cycling/swimming/...) land here
    # instead of being dropped. Same shape as apple_health_features'
    # workout_* entries. ===
    "workout_other_duration": {
        "raw_form": "Workout duration (s), uncategorized type",
        "processing": "Store as-is",
        "aggregation": AGG_SUM,
        "unit": "s",
        "display_unit": "min",
        "format": "{:.1f}",
        "display_scale": 1 / 60,
        "category": "Workouts",
        "emoji": "\U0001f3cb",
        "description": "Other workout duration",
    },
    "workout_other_distance": {
        "raw_form": "Workout distance (m), uncategorized type",
        "processing": "Store as-is",
        "aggregation": AGG_SUM,
        "unit": "m",
        "display_unit": "km",
        "format": "{:.2f}",
        "display_scale": 0.001,
        "category": "Workouts",
        "emoji": "\U0001f3cb",
        "description": "Other workout distance",
    },
    "workout_other_energy": {
        "raw_form": "Workout energy burned (kcal), uncategorized type",
        "processing": "Store as-is",
        "aggregation": AGG_SUM,
        "unit": "kcal",
        "display_unit": "kcal",
        "format": "{:.0f}",
        "category": "Workouts",
        "emoji": "\U0001f3cb",
        "description": "Other workout energy burned",
    },
}


def get_conversion_factor(series_type: str) -> float:
    """Multiplier applied to an OW value for ``series_type`` before storage."""
    return SERIES_TYPE_CONVERSION.get(series_type, 1.0)


def resolve_feature_type(series_type: str) -> str | None:
    """Resolve an OW series type string to the HiMe feature_type it should be
    stored under, or ``None`` if it's unmapped (caller should skip + debug-log)."""
    if series_type in UNMAPPED_SERIES_TYPES:
        return None
    if series_type in SERIES_TYPE_MAP:
        return SERIES_TYPE_MAP[series_type]
    if series_type in OW_FEATURE_SPEC:
        return series_type
    return None


def provider_allowlist_from_settings() -> frozenset[str] | None:
    """Parse ``OPENWEARABLES_PROVIDERS`` (comma-separated) into a lowercase
    allowlist, or ``None`` when unset (meaning: allow every provider except
    the natively-owned ``apple``/``apple_health`` — see
    :func:`backend.data_sources.open_wearables.mapper._provider_allowed`)."""
    raw = (settings.OPENWEARABLES_PROVIDERS or "").strip()
    if not raw:
        return None
    return frozenset(p.strip().lower() for p in raw.split(",") if p.strip())
